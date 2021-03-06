# Copyright 2017 Google Inc. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
r"""Sample device that consumes configuration from Google Cloud IoT.
This example represents a simple device with a temperature sensor and a fan
(simulated with software). When the device's fan is turned on, its temperature
decreases by one degree per second, and when the device's fan is turned off,
its temperature increases by one degree per second.

Every second, the device publishes its temperature reading to Google Cloud IoT
Core. The server meanwhile receives these temperature readings, and decides
whether to re-configure the device to turn its fan on or off. The server will
instruct the device to turn the fan on when the device's temperature exceeds 10
degrees, and to turn it off when the device's temperature is less than 0
degrees. In a real system, one could use the cloud to compute the optimal
thresholds for turning on and off the fan, but for illustrative purposes we use
a simple threshold model.

To connect the device you must have downloaded Google's CA root certificates,
and a copy of your private key file. See cloud.google.com/iot for instructions
on how to do this. Run this script with the corresponding algorithm flag.

  $ python cloudiot_pubsub_example_mqtt_device.py \
      --project_id=my-project-id \
      --registry_id=example-my-registry-id \
      --device_id=my-device-id \
      --private_key_file=rsa_private.pem \
      --algorithm=RS256

With a single server, you can run multiple instances of the device with
different device ids, and the server will distinguish them. Try creating a few
devices and running them all at the same time.
"""

import argparse
import datetime
import json
import os
import ssl
import time
import serial
from threading import Thread
import re

import jwt
import paho.mqtt.client as mqtt

import board
import busio

import adafruit_vcnl4010
import datetime
from envirophat import motion
from envirophat import leds


def create_jwt(project_id, private_key_file, algorithm):
    """Create a JWT (https://jwt.io) to establish an MQTT connection."""
    token = {
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(minutes=60),
        'aud': project_id
    }
    with open(private_key_file, 'r') as f:
        private_key = f.read()
    print('Creating JWT using {} from private key file {}'.format(
        algorithm, private_key_file))
    return jwt.encode(token, private_key, algorithm=algorithm)


def error_str(rc):
    """Convert a Paho error to a human readable string."""
    return '{}: {}'.format(rc, mqtt.error_string(rc))


class RemoteDevice(object):
    """Represents the state of a single remote device."""

    def __init__(self, shouldStart=True):
        self.temperature = 0
        self.pressure = 0
        self.humidity = 0
        self.gas_resistance = 0
        self.altitude = 0
        self.connected = False
        if shouldStart:
            self.ser = serial.Serial('/dev/ttyACM0', 115200)
            thread = Thread(target = self.check_incoming_serial_data)
            thread.start()

    def process_input_sensor_data(self, input_data):
        input_data = input_data.split(':',1)[-1]
        print (input_data)
        [self.temperature, self.pressure, self.humidity, self.gas_resistance, self.altitude] = re.findall(r"[-+]?\d*\.\d+|\d+", input_data)

    def check_incoming_serial_data(self):
        while(1):
            if(self.ser.in_waiting > 0):
                try:
                    line = self.ser.readline().decode().strip("\r\n")
                    self.process_input_sensor_data(line)
                except Exception as e:
                    print(e)
                    pass

class Device(object):
    """Represents the state of a single device."""

    def __init__(self):
        self.proximity = 0
        self.ambient_lux = 0
        self.led_on = False
        self.connected = False
        #self.i2c = busio.I2C(board.SCL, board.SDA)
        #self.sensor = adafruit_vcnl4010.VCNL4010(self.i2c)
        self.accel_x = 0
        self.accel_y = 0
        self.accel_z = 0

    def update_sensor_data(self):
        """Pretend to read the device's sensor data.
        If the fan is on, assume the temperature decreased one degree,
        otherwise assume that it increased one degree.
        """
        #self.proximity = self.sensor.proximity
        #self.ambient_lux = self.sensor.ambient_lux
        self.accel_x, self.accel_y, self.accel_z = motion.accelerometer()


    def wait_for_connection(self, timeout):
        """Wait for the device to become connected."""
        total_time = 0
        while not self.connected and total_time < timeout:
            time.sleep(1)
            total_time += 1

        if not self.connected:
            raise RuntimeError('Could not connect to MQTT bridge.')

    def on_connect(self, unused_client, unused_userdata, unused_flags, rc):
        """Callback for when a device connects."""
        print('Connection Result:', error_str(rc))
        self.connected = True

    def on_disconnect(self, unused_client, unused_userdata, rc):
        """Callback for when a device disconnects."""
        print('Disconnected:', error_str(rc))
        self.connected = False

    def on_publish(self, unused_client, unused_userdata, unused_mid):
        """Callback when the device receives a PUBACK from the MQTT bridge."""
        print('Published message acked.')

    def on_subscribe(self, unused_client, unused_userdata, unused_mid,
                     granted_qos):
        """Callback when the device receives a SUBACK from the MQTT bridge."""
        print('Subscribed: ', granted_qos)
        if granted_qos[0] == 128:
            print('Subscription failed.')

    def on_message(self, unused_client, unused_userdata, message):
        """Callback when the device receives a message on a subscription."""
        payload = message.payload
        print('Received message \'{}\' on topic \'{}\' with Qos {}'.format(
            payload, message.topic, str(message.qos)))
        # The device will receive its latest config when it subscribes to the
        # config topic. If there is no configuration for the device, the device
        # will receive a config with an empty payload.
        if not payload:
            print("No payload")
            return
        # The config is passed in the payload of the message. In this example,
        # the server sends a serialized JSON string.
        try:
            data = json.loads(payload.decode('utf-8'))
        except Exception as e:
            print ("Exception caught: "+str(e))
        if data['led_on'] != self.led_on:
            # If changing the state of the led, print a message and
            # update the internal state.
            self.led_on = data['led_on']
            if self.led_on:
                print('Led turned on.')
                leds.on()

            else:
                print('Led turned off.')
                leds.off()

def parse_command_line_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Example Google Cloud IoT MQTT device connection code.')
    parser.add_argument(
        '--project_id',
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        required=True,
        help='GCP cloud project name.')
    parser.add_argument(
        '--registry_id', required=True, help='Cloud IoT registry id')
    parser.add_argument(
        '--device_id',
        required=True,
        help='Cloud IoT device id')
    parser.add_argument(
        '--private_key_file', required=True, help='Path to private key file.')
    parser.add_argument(
        '--algorithm',
        choices=('RS256', 'ES256'),
        required=True,
        help='Which encryption algorithm to use to generate the JWT.')
    parser.add_argument(
        '--cloud_region', default='us-central1', help='GCP cloud region')
    parser.add_argument(
        '--ca_certs',
        default='roots.pem',
        help='CA root certificate. Get from https://pki.google.com/roots.pem')
    parser.add_argument(
        '--num_messages',
        type=int,
        default=100,
        help='Number of messages to publish.')
    parser.add_argument(
        '--mqtt_bridge_hostname',
        default='mqtt.googleapis.com',
        help='MQTT bridge hostname.')
    parser.add_argument(
        '--mqtt_bridge_port', type=int, default=8883, help='MQTT bridge port.')
    parser.add_argument(
        '--message_type', choices=('event', 'state'),
        default='event',
        help=('Indicates whether the message to be published is a '
              'telemetry event or a device state message.'))
    parser.add_argument(
        '--no_remote_device',
        type=bool,
        default=False,
        help='Is there an arduino attached?')
    return parser.parse_args()


def main():
    args = parse_command_line_args()

    # Create the MQTT client and connect to Cloud IoT.
    client = mqtt.Client(
        client_id='projects/{}/locations/{}/registries/{}/devices/{}'.format(
            args.project_id,
            args.cloud_region,
            args.registry_id,
            args.device_id))
    client.username_pw_set(
        username='unused',
        password=create_jwt(
            args.project_id,
            args.private_key_file,
            args.algorithm))
    client.tls_set(ca_certs=args.ca_certs, tls_version=ssl.PROTOCOL_TLSv1_2)

    device = Device()
    if not args.no_remote_device:
        remoteDevice = RemoteDevice()
    else:
        remoteDevice = RemoteDevice(False)

    client.on_connect = device.on_connect
    client.on_publish = device.on_publish
    client.on_disconnect = device.on_disconnect
    client.on_subscribe = device.on_subscribe
    client.on_message = device.on_message

    client.connect(args.mqtt_bridge_hostname, args.mqtt_bridge_port)

    client.loop_start()

    # This is the topic that the device will publish telemetry events
    # (temperature data) to.
    mqtt_telemetry_topic = '/devices/{}/events'.format(args.device_id)

    # This is the topic that the device will receive configuration updates on.
    mqtt_config_topic = '/devices/{}/config'.format(args.device_id)

    # Wait up to 5 seconds for the device to connect.
    device.wait_for_connection(5)

    # Subscribe to the config topic.
    client.subscribe(mqtt_config_topic, qos=1)

    # Update and publish temperature readings at a rate of one per second.
    for _ in range(args.num_messages):
        # In an actual device, this would read the device's sensors. Here,
        # you update the temperature based on whether the fan is on.
        device.update_sensor_data()

        # Report the device's temperature to the server by serializing it
        # as a JSON string.
        payload = json.dumps({'proximity': device.proximity,'luminance':device.ambient_lux, 'accel_x':device.accel_x,'accel_y':device.accel_y,'accel_z':device.accel_z,'time':datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'temperature':remoteDevice.temperature, 'pressure':remoteDevice.pressure, 'humidity':remoteDevice.humidity, 'gas_resistance':remoteDevice.gas_resistance, 'altitude':remoteDevice.altitude})
        print('Publishing payload', payload)
        print('on mqtt telemetry topic '+mqtt_telemetry_topic)
        client.publish(mqtt_telemetry_topic, payload, qos=1)
        # Send events every second.
        time.sleep(1)

    client.disconnect()
    client.loop_stop()
    print('Finished loop successfully. Goodbye!')


if __name__ == '__main__':
    main()
