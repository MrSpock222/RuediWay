import asyncio
import io
import json
import unittest
from unittest.mock import patch, MagicMock

from fastapi import BackgroundTasks, HTTPException
from PIL import Image

from app import server
from app.camera import CameraState
from app.live import LiveView
from app.pairing import PairingState
from app.results import ResultStore
from esp32.setup_device import validate, provision


class ESP32Tests(unittest.TestCase):
    def test_current_firmware_can_repair_pairing_without_resending_wifi(self):
        status = {'firmware':'ruediway-xiao-3', 'camera_ready':True, 'ssid':'saved-network',
                  'paired':True, 'wifi_connected':True}
        serial_port = MagicMock()
        serial_port.__enter__.return_value = serial_port
        response = io.BytesIO(b'{"code":"123456"}')
        with patch('serial.Serial', return_value=serial_port), \
             patch('esp32.setup_device.wait_event', side_effect=[status, {'event':'paired'}, status]), \
             patch('esp32.setup_device.urllib.request.urlopen', return_value=response):
            result = provision('COM6', 'http://192.168.1.10:8000', '', '', True, lambda _: None)
        self.assertTrue(result['paired'])
        commands = [json.loads(call.args[0]) for call in serial_port.write.call_args_list]
        pair_command = next(command for command in commands if command['cmd'] == 'pair')
        self.assertNotIn('password', pair_command)
        self.assertNotIn('ssid', pair_command)

    def setUp(self):
        self.camera = CameraState()
        self.camera.register('esp-token', 'xiao_esp32s3')
        self.camera.heartbeat('esp-token')
        self.results = ResultStore()

    def test_pairing_announces_capabilities_and_resets_pi_shutter(self):
        live = LiveView()
        live.configure({'ev':-4, 'shutter':1000, 'mode':'text'})
        with patch.object(server, 'CAMERA', self.camera), patch.object(server, 'LIVE', live), patch.object(server, 'PAIRING', PairingState()):
            code = server.PAIRING.generate()
            server.pair(server.PairRequest(code=code, device='camera', camera_model='xiao_esp32s3'))
            self.assertEqual(self.camera.snapshot()['name'], 'XIAO ESP32-S3 Sense')
            self.assertFalse(self.camera.snapshot()['manual_shutter'])
            self.assertEqual(live.options()['shutter'], 0)
            self.assertEqual(live.options()['ev'], -2)
            with self.assertRaises(HTTPException):
                server.camera_settings(server.CameraSettings(shutter=1000))

    def test_raw_upload_acknowledges_before_background_and_cannot_duplicate(self):
        raw = io.BytesIO()
        Image.new('RGB', (30, 20), 'white').save(raw, 'JPEG')
        class Request:
            async def stream(self):
                yield raw.getvalue()
        with patch.object(server, 'CAMERA', self.camera), patch.object(server, 'RESULTS', self.results), patch.object(server, 'analyze_image') as ai:
            job = self.camera.request('host', preview=True)['id']
            self.camera.claim('esp-token')
            tasks = BackgroundTasks()
            response = asyncio.run(server.camera_jpeg(job, Request(), tasks, 'esp-token'))
            self.assertEqual(response.status_code, 202)
            self.assertTrue(json.loads(response.body)['accepted'])
            self.assertIsNone(self.camera.preview())
            self.assertEqual(self.camera.snapshot()['state'], 'analyzing')
            with self.assertRaises(HTTPException):
                asyncio.run(server.camera_jpeg(job, Request(), BackgroundTasks(), 'esp-token'))
            asyncio.run(tasks())
            self.assertEqual(self.camera.preview()[0], job)
            ai.assert_not_called()

    def test_raw_photo_publishes_answer_and_failure_retains_previous(self):
        raw = io.BytesIO()
        Image.new('RGB', (30, 20), 'white').save(raw, 'JPEG')
        class Request:
            async def stream(self):
                yield raw.getvalue()
        with patch.object(server, 'CAMERA', self.camera), patch.object(server, 'RESULTS', self.results), patch.object(server, 'analyze_image', return_value='Answer') as ai:
            job = self.camera.request('watch')['id']
            self.camera.claim('esp-token')
            tasks = BackgroundTasks()
            asyncio.run(server.camera_jpeg(job, Request(), tasks, 'esp-token'))
            ai.assert_not_called()
            asyncio.run(tasks())
            self.assertEqual(self.results.snapshot()['answer'], 'Answer')
            self.assertFalse(self.camera.snapshot()['busy'])
            job = self.camera.request('watch')['id']
            self.camera.claim('esp-token')
            self.camera.begin('esp-token', job)
            server.finish_camera_jpeg(job, b'bad jpeg', 'esp-token', False, 'normal')
            self.assertEqual(self.camera.snapshot()['state'], 'error')
            self.assertEqual(self.results.snapshot()['answer'], 'Answer')

    def test_empty_upload_marks_job_failed(self):
        class Request:
            async def stream(self):
                yield b''
        with patch.object(server, 'CAMERA', self.camera):
            job = self.camera.request('host')['id']
            self.camera.claim('esp-token')
            with self.assertRaises(HTTPException):
                asyncio.run(server.camera_jpeg(job, Request(), BackgroundTasks(), 'esp-token'))
            self.assertFalse(self.camera.snapshot()['busy'])

    def test_usb_setup_validates_without_requiring_secret_on_repair(self):
        self.assertEqual(validate('http://192.168.1.2:8000/', 'WiFi', '12345678', False), 'http://192.168.1.2:8000')
        self.assertTrue(validate('http://192.168.1.2:8000', '', '', True))
        for url in ['http://localhost:8000','http://127.0.0.1:8000','http://user:secret@host','https://host','http://host/path']:
            with self.assertRaises(ValueError):
                validate(url, 'WiFi', '12345678', False)
        with self.assertRaises(ValueError):
            validate('http://192.168.1.2:8000', 'WiFi', 'short', False)
