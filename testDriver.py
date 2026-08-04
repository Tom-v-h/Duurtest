from __future__ import annotations

import xmlrpc.client
import time
import random

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import serial
from serial import SerialException

class RelayState(Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"

@dataclass

class RelayControllerConfig:
    port: str = 'COM11'
    baudrate: int = 115200
    timeout: float = 1.0
    write_timeout: float = 1.0
    startup_delay: float = 2.0

class RelayController():
    """
    Simple Python interface for the STM32 UART relay controller.
    Supported STM32 commands:
      - ON
      - OFF
      - TOGGLE
      - STATUS
      - HELP
    """
    def __init__(self, config = RelayControllerConfig):
        self.config = config
        self._serial: Optional[serial.Serial] = None

    def connect(self) -> None:
        if self.is_connected:
            return
        try:
            self._serial = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                timeout=self.config.timeout,
                write_timeout=self.config.write_timeout,
            )
            # Some STM32 boards reset when serial opens.
            time.sleep(self.config.startup_delay)
            self.clear_buffers()
        except SerialException as exc:
            raise ConnectionError(f"Could not open serial port {self.config.port}: {exc}") from exc
 
    def disconnect(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def clear_buffers(self) -> None:
        self._require_connection()
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
 
    def turn_on(self) -> str:
        return self.send_command("ON")

    def turn_off(self) -> str:
        return self.send_command("OFF")
 
    def toggle(self) -> str:
        return self.send_command("TOGGLE")
 
    def help(self) -> str:
        return self.send_command("HELP")
 
    def get_status(self) -> RelayState:
        response = self.send_command("STATUS")
        upper_response = response.upper()
        if "ON" in upper_response:
            return RelayState.ON
        if "OFF" in upper_response:
            return RelayState.OFF
        return RelayState.UNKNOWN

    def send_command(self, command: str) -> str:
        self._require_connection()
        clean_command = command.strip()
        if not clean_command:
            raise ValueError("Command may not be empty")
        full_command = f"{clean_command}\r\n"
        self._serial.write(full_command.encode("utf-8"))
        self._serial.flush()
        return self._read_response()

    def _read_response(self) -> str:
        """
        Reads until the STM32 prompt '>' is received or timeout happens.
        The firmware from the previous answer prints:
            response text
            >
        after each command.
        """
        self._require_connection()
        received = bytearray()
        deadline = time.time() + self.config.timeout
        while time.time() < deadline:
            if self._serial.in_waiting > 0:
                chunk = self._serial.read(self._serial.in_waiting)
                received.extend(chunk)
                if b">" in received:
                    break
            else:
                time.sleep(0.01)
        text = received.decode("utf-8", errors="replace")
        return self._clean_response(text)

    @staticmethod
    def _clean_response(response: str) -> str:
        """
        Removes echoed command prompt clutter as much as possible.
        """
        lines = response.replace("\r", "").split("\n")
        cleaned_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == ">":
                continue
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines)

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise RuntimeError("RelayController is not connected")

    def __enter__(self) -> "RelayController":
        self.connect()
        return self
 
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()

colours = [['CX01', 1], ['MH01', 2], ['YH04', 3], ['RH01', 4], ['YX01', 5], ['WX01', 6], ['CH01', 7], ['GH01', 8], ['BH01', 9], ['OH01', 10], ['RX01', 11], ['YH01', 12], ['GX01', 13], ['BX01', 14], ['YH02', 15], ['DISP16', 16]]
relay = RelayController()
relay.connect()

while True:
    relay.turn_on()
    time.sleep(10)
    
    server = xmlrpc.client.ServerProxy("http://localhost:9111/", allow_none=True, verbose=True)
    server.connect('COM12', '0x0002', 19200)
    server.poll()
    dispensed = 0
    
    dispensed = dispensed + 1
    print('Dispensed: ', dispensed)

    dosingunit = random.choice(colours)
    amount = random.randint(1,500)
    
    server.correctFillLevel(dosingunit[0], dosingunit[1], 3800)
    server.prepareUnitForDispense(dosingunit[0], amount)
    server.dispenseAllPreparedUnits()
    time.sleep(60)
    relay.turn_off()
    time.sleep(5)
