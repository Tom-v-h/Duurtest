"""
Serial communication with the STM32 relay board.

The relay switches the mains power to the dosing units, which is what makes
power cycling during an endurance test possible. The firmware accepts plain
text commands (ON, OFF, TOGGLE, STATUS, HELP) and answers with a line of
text followed by a '>' prompt.
"""

import serial
from serial import SerialException
import time
from typing import Optional

class RelayControllerConfig:
    """
    Connection settings. These are the defaults; the caller overrides port
    and baudrate with whatever was picked in the GUI.
    """
    port: str = 'COM11'
    baudrate: int = 115200
    timeout: float = 1.0          # seconds to wait for a response
    write_timeout: float = 1.0    # seconds to wait while sending
    startup_delay: float = 2.0    # pause after opening, see connect()

class RelayController:
    """Opens the serial port and sends commands to the relay board."""

    def __init__(self, config = RelayControllerConfig):
            self.config = config
            self._serial: Optional[serial.Serial] = None

    def connect(self) -> None:
            """Open the serial port. Does nothing when already connected."""
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
            """Close the serial port and release it for other programs."""
            if self._serial is not None:
                self._serial.close()
                self._serial = None

    @property
    def is_connected(self) -> bool:
        """True when the port is open and ready to use."""
        return self._serial is not None and self._serial.is_open

    def clear_buffers(self) -> None:
        """Throw away anything still queued, so a reply cannot be mistaken
        for the answer to the next command."""
        self._require_connection()
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def turn_on(self) -> str:
        """Switch the relay on; returns the board's answer."""
        return self.send_command("ON")

    def turn_off(self) -> str:
        """Switch the relay off; returns the board's answer."""
        return self.send_command("OFF")


    def send_command(self, command: str) -> str:
        """Send one command and return the response text."""
        self._require_connection()
        clean_command = command.strip()
        if not clean_command:
            raise ValueError("Command may not be empty")
        # The firmware expects every command to end with a carriage return
        # and newline.
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
        # Collect bytes until the prompt arrives or the timeout expires,
        # whichever comes first.
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
            # Drop empty lines and the bare prompt; keep the actual answer.
            if not stripped:
                continue
            if stripped == ">":
                continue
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines)

    def _require_connection(self) -> None:
        """Guard used by every method that needs an open port."""
        if not self.is_connected:
            raise RuntimeError("RelayController is not connected")
    