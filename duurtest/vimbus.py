import enum
import logging

import serial

from typing import Optional

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

_KEY = bytearray.fromhex(
    "BE2A9BED404D572D4761EE90AF5E35FD6F5B194C7EB67704EDEB25E08BC399D1")
_IV = bytearray.fromhex("BE2A9BED404D572D4761EE90AF5E35FD")

_VAR_TYPE_OPT_OFFSET = 100


class VariableType(enum.Enum):
    STRING = 1
    U8 = 2
    U16 = 3
    U32 = 4
    U64 = 5

    OPT_STRING = STRING + _VAR_TYPE_OPT_OFFSET
    OPT_U8 = U8 + _VAR_TYPE_OPT_OFFSET
    OPT_U16 = U16 + _VAR_TYPE_OPT_OFFSET
    OPT_U32 = U32 + _VAR_TYPE_OPT_OFFSET
    OPT_U64 = U64 + _VAR_TYPE_OPT_OFFSET


class FormatHint(enum.Enum):
    DEFAULT = 1
    DECIMAL = 2
    BOOLEAN = 3


class EnumFormat:
    enum_class: type[enum.Enum]
    values: dict[int, str]

    def __init__(self, enum_class: type[enum.Enum], values: dict[int, str]):
        self.enum_class = enum_class
        self.values = values


class Command:
    """
    VIMBus command
    """

    definition: 'CommandDefinition'
    addr: int
    vars: dict[str, int | str | enum.Enum | None]

    def __init__(self, definition: 'CommandDefinition', addr: int, vars: dict[str, int | str | None]):
        """
        Constructor
        :param definition: Command definition
        :param addr: Command destination address
        :param vars: Command variables
        """
        self.definition = definition
        self.addr = addr
        self.vars = vars

    def human_readable(self) -> str:
        """
        Returns a human-readable representation of the command
        :return: Human-readable representation
        """
        return self.definition.human_readable([self.vars[var_name]
                                               for (var_name, _, _) in self.definition.vars
                                               if self.vars[var_name] is not None],
                                              self.addr)

    def set_variable_count(self) -> int:
        return len([v for v in self.vars.values() if v is not None])


class LogMessage:
    """
    VIMBus log message
    """

    definition: 'LogMessageDefinition'
    addr: int
    vars: dict[str, int | str | None]

    def __init__(self, definition: 'LogMessageDefinition', addr: int, vars: dict[str, int | str | None]):
        """
        Constructor
        :param definition: Command definition
        :param addr: Command destination address
        :param vars: Command variables
        """
        self.definition = definition
        self.addr = addr
        self.vars = vars

    def human_readable(self) -> str:
        """
        Returns a human-readable representation of the command
        :return: Human-readable representation
        """
        return self.definition.human_readable([self.vars[var_name]
                                               for (var_name, _, _) in self.definition.vars
                                               if self.vars[var_name] is not None])


class RawLogMessage:
    """
    Raw VIMBus log message
    """

    addr: int
    code: int
    raw_vars: list[str]

    def __init__(self, addr: int, code: int, raw_vars: list[str]):
        self.addr = addr
        self.code = code
        self.raw_vars = raw_vars


class MessageDefinitionBase:
    vars: list[tuple[str, VariableType, FormatHint | EnumFormat]]

    @staticmethod
    def decrypt_parts(command: str) -> list[str]:
        parts = command.split(';')

        if len(parts) != 4:
            raise Exception(f"Expected encrypted string '{command}' to contain 4 parts, got {len(parts)}")

        cipher = AES.new(_KEY, AES.MODE_CBC, _IV)
        data = bytes.fromhex(parts[2])
        data = cipher.decrypt(data).decode('utf8').rstrip('\0')
        return [parts[0], parts[1], *data.split(';'), parts[3]]

    def _encode_vars(self, vars: list[int | str | None]) -> list[str]:
        n_required_variables = len(
            [vt for _, vt, _ in self.vars if not _var_is_optional(vt)])
        n_vars_min = n_required_variables
        n_vars_max = len(self.vars)

        if len(vars) < n_vars_min or len(vars) > n_vars_max:
            raise Exception(f"Wrong number of variables, expected between {n_vars_min} and {n_vars_max}, "
                            f"got {len(vars)}")

        return [self._encode_var(val, var_name, var_type)
                for val, (var_name, var_type, _)
                in zip(vars, self.vars) if val is not None]

    def _encode_var(self, val: str | int | None, var_name: str, var_type: VariableType) -> str | None:
        if val is None:
            if not _var_is_optional(var_type):
                raise Exception(
                    f"Got None value for non-optional variable {var_name}")

            return None

        var_base_type = _var_base_type(var_type)

        if var_base_type == VariableType.STRING:
            # Validate the value is a string
            if not isinstance(val, str):
                raise Exception(
                    f"Expected value for variable {var_name} to be a string, got value '{val}' "
                    f"of type '{type(val).__name__}'")

            return val
        elif var_base_type in {VariableType.U8, VariableType.U16, VariableType.U32}:
            # Validate the value is an integer
            if not isinstance(val, int):
                raise Exception(
                    f"Expected value for variable {var_name} to be an integer, "
                    f"got value '{val}' of type '{type(val).__name__}'")

            # Validate integer is positive
            if val < 0:
                raise Exception(f"Expected value for variable {var_name} to be an unsigned integer, got negative "
                                f"value '{val}'")

            # Validate integer is below upper bound
            limits = {
                VariableType.U8: (1 << 8) - 1,
                VariableType.U16: (1 << 16) - 1,
                VariableType.U32: (1 << 32) - 1,
                VariableType.U64: (1 << 64) - 1,
            }

            if val > limits[var_base_type]:
                raise Exception(f"Expected value for variable {var_name} to have an upper limit of "
                                f"{limits[var_base_type]}, got value '{val}'")

            # Encode
            digits = {
                VariableType.U8: 2,
                VariableType.U16: 4,
                VariableType.U32: 8,
                VariableType.U64: 16,
            }

            return f'{val:x}'.upper().rjust(digits[var_base_type], '0')

    def _decode_var(self,
                    val: str,
                    var_name: str,
                    var_type: VariableType,
                    hint: Optional[FormatHint | EnumFormat] = None) -> int | str | enum.Enum | None:
        base_var_type = _var_base_type(var_type)

        if base_var_type == VariableType.STRING:
            return val
        elif base_var_type in {VariableType.U8, VariableType.U16, VariableType.U32, VariableType.U64}:
            # Check number of digits
            digits = {
                VariableType.U8: 2,
                VariableType.U16: 4,
                VariableType.U32: 8,
                VariableType.U64: 16,
            }

            if len(val) != digits[base_var_type]:
                raise Exception(f"Expected {digits[base_var_type]} digits in integer variable, got value '{val}' with "
                                f"{len(val)} digits")

            try:
                int_val = int(val, 16)
                if hint is not None and isinstance(hint, EnumFormat):
                    return hint.enum_class(int_val)
                else:
                    return int_val
            except ValueError as ve:
                raise Exception(
                    f"Failed to parse hexadecimal number '{val}': {str(ve)}")

    def _human_readable_val(self, val: int | str | enum.Enum | None, var_type: VariableType,
                            format_hint: FormatHint | EnumFormat) -> str:
        var_base_type = _var_base_type(var_type)
        if var_base_type == VariableType.STRING:
            return f'"{val}"'
        else:
            if isinstance(format_hint, EnumFormat):
                int_val = val.value
                hex_val = f'{int_val:x}'.upper()
                if int_val in format_hint.values:
                    return f'0x{hex_val} ({format_hint.values[int_val]})'
                else:
                    return f'0x{hex_val} (?)'
            if format_hint == FormatHint.DECIMAL:
                return f'{val:,}'
            if format_hint == FormatHint.BOOLEAN:
                if val == 0:
                    return 'False'
                else:
                    return 'True'
            hex_val = f'{val:x}'.upper()
            return f'0x{hex_val}'

    def _add_default_hint(self, v: tuple[str, VariableType] | tuple[str, VariableType, FormatHint | EnumFormat]) -> \
            tuple[
                str, VariableType, FormatHint | EnumFormat]:
        if len(v) == 2:
            name, var_type = v
            return name, var_type, FormatHint.DEFAULT
        else:
            return v

    def _crc16(self, string: str):
        poly = 0xA001
        data = bytes(string, 'ascii')

        crc = 0
        for byte in data:
            crc ^= byte
            for i in range(8):
                if crc & 0b1 == 0b1:
                    crc = (crc >> 1) ^ poly
                else:
                    crc >>= 1

        return crc


class CommandDefinition(MessageDefinitionBase):
    """
    Definition of a VIMBus command
    """

    name: str
    code: int

    def __init__(self, name: str, code: int,
                 vars: list[tuple[str, VariableType] | tuple[str, VariableType, FormatHint | EnumFormat]]):
        """
        Constructor
        :param name: Command name
        :param code: Command code
        :param vars: Command variable names and types
        """

        self.name = name
        self.code = code
        self.vars = [self._add_default_hint(v) for v in vars]

    def human_readable(self, vars: list[int | str | None], addr: int = 0) -> str:
        """
        Returns a human-readable version of a command
        :param vars: Command variables
        :param addr: Command destination address
        :return: Human-readable version of command
        """

        vars_readable = ', '.join(
            [f'{name} = {self._human_readable_val(val, var_type, format_hint)}' for (name, var_type, format_hint), val
             in zip(self.vars, vars)])

        return f'{self.name}({vars_readable}) @ {hex(addr)}'

    def encode(self, vars: list[int | str | None], address: int = 0, encrypt: bool = False) -> str:
        """
        Creates a command and returns the encoded string
        :param vars: Variable values
        :param address: Destination address of the command
        :param encrypt: Whether the command should be encrypted
        :return: Encoded command string
        """

        # Create base command with address, command code and variables
        encoded_addr = self._encode_var(address, 'address', VariableType.U16)
        encoded_cmd_code = self._encode_var(
            self.code, 'code', VariableType.U16)
        encoded_vars = self._encode_vars(vars)
        cmd = ';'.join([encoded_cmd_code, *encoded_vars])

        if encrypt:
            cipher = AES.new(_KEY, AES.MODE_CBC, _IV)
            if len(cmd) % 16 != 0:
                padding = "\0" * (16 - (len(cmd) % 16))
                cmd = f'{cmd}{padding}'
            cmd = cipher.encrypt(bytes(cmd, 'utf8')).hex().upper()

        cmd = f'{encoded_addr};{cmd}'

        # Add command length
        cmd_len = len(cmd) + 5 + 7
        encoded_cmd_len = self._encode_var(cmd_len, 'length', VariableType.U16)
        cmd = f'{encoded_cmd_len};{cmd};'

        # Add CRC
        cmd_crc = self._crc16(cmd)
        encoded_cmd_crc = self._encode_var(cmd_crc, 'crc', VariableType.U16)

        return f'{cmd}{encoded_cmd_crc}\r\n'

    def decode(self, command: str, decrypt: bool = False) -> Command | RawLogMessage:
        """
        Decodes a command string to a command
        :param command: Command string to decode
        :return: Decoded command
        """
        if decrypt:
            parts = self.decrypt_parts(command)
        else:
            parts = command.split(';')

        # Check for log messages
        addr = self._decode_var(parts[1], 'address', VariableType.U16)
        code = self._decode_var(parts[2], 'code', VariableType.U16)
        if code == 0:
            return RawLogMessage(addr, code, parts[3:-1])

        n_required_variables = len(
            [vt for _, vt, _ in self.vars if not _var_is_optional(vt)])
        n_parts_min = n_required_variables + 4
        n_parts_max = len(self.vars) + 4

        if len(parts) < n_parts_min or len(parts) > n_parts_max:
            raise Exception(f"Expected command string '{command}' to contain between {n_parts_min} and {n_parts_max} "
                            f"parts, got {len(parts)}")

        # Parse and check command length
        if not decrypt:
            cmd_length = self._decode_var(parts[0], 'length', VariableType.U16)
            if len(command) != cmd_length:
                raise Exception(
                    f"Wrong length of command string '{command}', expected {cmd_length}, got {len(command)}")

        # Parse and check command address and code
        if code != self.code:
            raise Exception(
                f"Wrong code in command string '{command}', expected {self.code}, got {code}")

        # Parse variables
        decoded_vars = {var_name: self._decode_var(val, var_name, var_type, var_hint) for
                        val, (var_name, var_type, var_hint) in
                        zip(parts[3:-1], self.vars)}
        for var_name, _, _ in self.vars[len(parts[3:-1]):]:
            decoded_vars[var_name] = None

        # Parse and check CRC
        decoded_crc = self._decode_var(
            parts[-1].rstrip('\r\n'), 'crc', VariableType.U16)
        calculated_crc = self._crc16(command[0:-6])

        if decoded_crc != calculated_crc:
            raise Exception(
                f"Expected CRC of command string '{command}' to be {calculated_crc}, got {decoded_crc}")

        return Command(self, addr, decoded_vars)


class LogMessageDefinition(MessageDefinitionBase):
    name: str
    code: int

    def __init__(
            self,
            name: str,
            code: int,
            vars: list[tuple[str, VariableType] | tuple[str, VariableType, FormatHint | EnumFormat]]):
        """
        Constructor
        :param name: Log message name
        :param code: Log message code
        :param vars: Log message variable names and types
        """

        self.name = name
        self.code = code
        self.vars = [self._add_default_hint(v) for v in vars]

    def human_readable(self, vars: list[int | str | None], addr: int = 0) -> str:
        """
        Returns a human-readable version of a log message
        :param vars: Command variables
        :param addr: Command destination address
        :return: Human-readable version of command
        """

        vars_readable = ', '.join(
            [f'{name} = {self._human_readable_val(val, var_type, format_hint)}' for (name, var_type, format_hint), val
             in
             zip(self.vars, vars)])

        return f'{self.name}({vars_readable})'

    def decode(self, command: str, decrypt: bool = False) -> LogMessage:
        """
        Decodes a command string to a log message
        :param command: Command string to decode
        :return: Decoded command
        """
        if decrypt:
            parts = self.decrypt_parts(command)
        else:
            parts = command.split(';')

        # Validate number of parts
        n_required_variables = len(
            [vt for _, vt, _ in self.vars if not _var_is_optional(vt)])
        n_parts_min = n_required_variables + 5
        n_parts_max = len(self.vars) + 5

        if len(parts) < n_parts_min or len(parts) > n_parts_max:
            raise Exception(f"Expected command string '{command}' to contain between {n_parts_min} and {n_parts_max} "
                            f"parts, got {len(parts)}")

        # Parse and check command length
        if not decrypt:
            cmd_length = self._decode_var(parts[0], 'length', VariableType.U16)
            if len(command) != cmd_length:
                raise Exception(
                    f"Wrong length of command string '{command}', expected {cmd_length}, got {len(command)}")

        addr = self._decode_var(parts[1], 'address', VariableType.U16)
        code = self._decode_var(parts[2], 'code', VariableType.U16)
        log_id = self._decode_var(parts[3], 'logid', VariableType.U16)

        # Parse and check command address and code
        if code != 0:
            raise Exception(f"Wrong command code in log string '{command}', expected 0, got {code}")

        if log_id != self.code:
            raise Exception(
                f"Wrong code in command string '{command}', expected {self.code}, got {code}")

        # Parse variables
        decoded_vars = {var_name: self._decode_var(val, var_name, var_type, var_hint) for
                        val, (var_name, var_type, var_hint) in
                        zip(parts[4:-1], self.vars)}
        for var_name, _, _ in self.vars[len(parts[4:-1]):]:
            decoded_vars[var_name] = None

        # Parse and check CRC
        decoded_crc = self._decode_var(
            parts[-1].rstrip('\r\n'), 'crc', VariableType.U16)
        calculated_crc = self._crc16(command[0:-6])

        if decoded_crc != calculated_crc:
            raise Exception(
                f"Expected CRC of command string '{command}' to be {calculated_crc}, got {decoded_crc}")

        return LogMessage(self, addr, decoded_vars)


class NackReason(enum.Enum):
    LENGTH_INCORRECT = 0x01
    CRC_INCORRECT = 0x02
    TIMEOUT = 0x03
    CMD_CODE_INCORRECT = 0x04
    VARIABLE_ERROR = 0x05
    VARIABLE_COUNT_INCORRECT = 0x06
    INTERNAL_ERROR = 0x07
    UNKNOWN_ERROR = 0xFF


NACK_REASON = EnumFormat(NackReason, {
    0x01: 'Length incorrect',
    0x02: 'CRC incorrect',
    0x03: 'Timeout',
    0x04: 'Command incorrect',
    0x05: 'Variable error',
    0x06: 'Variable count incorrect',
    0x07: 'Internal error',
    0xFF: 'Unknown error'
})

ACK = CommandDefinition('Ack', 0x0001, [])
NACK = CommandDefinition('Nack', 0x0002, [('reason', VariableType.U8, NACK_REASON)])

POLL_REQ = CommandDefinition('Poll', 0x000A, [])

GET_FIRMWARE_VERSION_REQ = CommandDefinition('GetFirmwareVersion', 0x00A0, [])
GET_FIRMWARE_VERSION_RES = CommandDefinition('GetFirmwareVersion', 0x00A0, [
    ('version', VariableType.STRING), ('timestamp', VariableType.STRING)])

GET_UID_REQ = CommandDefinition('GetUID', 0x00A3, [])
GET_UID_RES = CommandDefinition('GetUID', 0x00A3, [('uid', VariableType.STRING)])

JUMP_TO_BOOTLOADER_REQ = CommandDefinition('JumpToBootloader', 0xAABB, [])

REBOOT_REQ = CommandDefinition('Reboot', 0xAAAA, [])

ENTER_UPDATE_MODE_REQ = CommandDefinition('EnterUpdateMode', 0x00C0, [])
ENTER_UPDATE_MODE_RES = CommandDefinition('EnterUpdateMode', 0x00C0, [])


class NodeState(enum.Enum):
    ONLINE = 0x0001

    BOOTLOADER_UPDATE_MODE = 0x0B01
    BOOTLOADER_RECOVERY_MODE = 0x0B02
    BOOTLOADER_ERROR = 0x0BEE

    OFFLINE = 0x0E00
    UNKNOWN = 0x0E01


SLAVE_STATE = EnumFormat(NodeState, {
    0x0001: 'Online',
    0x0B01: 'Bootloader Update Mode',
    0x0B02: 'Bootloader Recovery Mode',
    0x0BEE: 'Bootloader Error',
    0x0E00: 'Offline',
    0x0E01: 'Unknown'
})

GET_SLAVE_STATE_REQ = CommandDefinition('GetSlaveState', 0x00A4, [])
GET_SLAVE_STATE_RES = CommandDefinition('GetSlaveState', 0x00A4, [('state', VariableType.U16, SLAVE_STATE)])


class BootloaderResult(enum.Enum):
    OK = 0x0001

    UPDATE_COMPLETE = 0xB001

    ERR_NO_UPDATE_IN_PROGRESS = 0xBE00
    ERR_INVALID_UPDATE_TYPE = 0xBE01
    ERR_UPDATE_IN_PROGRESS = 0xBE02
    ERR_UPDATE_NOT_DONE_YET = 0xBE03

    ERR_DATA_SIZE_MISMATCH = 0xBE10
    ERR_DATA_NOT_VALID_BASE64 = 0xBE11
    ERR_DATA_CRC_MISMATCH = 0xBE12

    ERR_WRITE_TO_WRONG_ADDR = 0xBE20
    ERR_WRITE_WILL_OVERRUN = 0xBE21
    ERR_FAILED_TO_ERASE_FLASH = 0xBE22
    ERR_FAILED_TO_WRITE_FLASH = 0xBE23
    ERR_FLASH_CRC_MISMATCH = 0xBE24

    ERR_APP_CRC_INVALID = 0xBEA0
    ERR_APP_CRC_STATE_INVALID = 0xBEA1
    ERR_APP_RESET_HANDLER_ADDR_INVALID = 0xBEA2
    ERR_APP_INITIAL_STACK_PTR_INVALID = 0xBEA3
    ERR_APP_UNKNOWN_FAULT = 0xBEAF

    ERR_INTERNAL = 0xBEFF


BL_UPDATE_RESULT = EnumFormat(BootloaderResult, {
    0x0001: 'Ok',

    0xB001: 'Update Complete',

    0xBE00: 'Error: No update in progress',
    0xBE01: 'Error: Invalid update type',
    0xBE02: 'Error: Update already in progress',
    0xBE03: 'Error: Update not done yet',

    0xBE10: 'Error: Data size doesnt match',
    0xBE11: 'Error: Data is not a valid base 64 string',
    0xBE12: 'Error: Data CRC mismatch',

    0xBE20: 'Error: Write to wrong address',
    0xBE21: 'Error: Write will overrun updating segment',
    0xBE22: 'Error: Failed to erase flash',
    0xBE23: 'Error: Failed to write flash',
    0xBE24: 'Error: CRC of written flash mismatch',

    0xBEA0: 'Error: Application CRC invalid',
    0xBEA1: 'Error: Application CRC state invalid',
    0xBEA2: 'Error: Reset handler address invalid',
    0xBEA3: 'Error: Initial stack pointer invalid',
    0xBEAF: 'Error: Unknown application fault',

    0xBEFF: 'Error: Internal error'
})

BL_START_UPDATE_REQ = CommandDefinition('BootloaderStartUpdate', 0x0CA0, [('update_type', VariableType.U16)])
BL_START_UPDATE_RES = CommandDefinition('BootloaderStartUpdate', 0x0CA0, [
    ('result', VariableType.U16, BL_UPDATE_RESULT),
    ('next_offset', VariableType.U32),
    ('binary_size', VariableType.U32)])

BL_WRITE_REQ = CommandDefinition('BootloaderWrite', 0x0CA1, [
    ('offset', VariableType.U32),
    ('size', VariableType.U32, FormatHint.DECIMAL),
    ('data', VariableType.STRING),
    ('crc', VariableType.U16)])
BL_WRITE_RES = CommandDefinition('BootloaderWrite', 0x0CA1, [
    ('result', VariableType.U16, BL_UPDATE_RESULT),
    ('next_offset', VariableType.U32)])

BL_FINISH_UPDATE_REQ = CommandDefinition('BootloaderFinishUpdate', 0x0CA2, [])
BL_FINISH_UPDATE_RES = CommandDefinition('BootloaderFinishUpdate', 0x0CA2, [
    ('result', VariableType.U16, BL_UPDATE_RESULT)])

BL_CANCEL_UPDATE_REQ = CommandDefinition('BootloaderCancelUpdate', 0x0CA3, [])
BL_CANCEL_UPDATE_RES = CommandDefinition('BootloaderCancelUpdate', 0x0CA3, [
    ('result', VariableType.U16, BL_UPDATE_RESULT)])

BL_VALIDATE_APP_REQ = CommandDefinition('BootloaderValidateApplication', 0x0CA4, [])
BL_VALIDATE_APP_RES = CommandDefinition('BootloaderValidateApplication', 0x0CA4, [
    ('result', VariableType.U16, BL_UPDATE_RESULT)])

BL_BOOT_TO_APP_REQ = CommandDefinition('BootloaderBootToApp', 0x0CAA, [])
BL_BOOT_TO_APP_RES = CommandDefinition('BootloaderBootToApp', 0x0CAA, [
    ('result', VariableType.U16, BL_UPDATE_RESULT)])


def _var_is_optional(var_type: VariableType) -> bool:
    return var_type.value >= _VAR_TYPE_OPT_OFFSET


def _var_base_type(var_type: VariableType) -> VariableType:
    if var_type.value >= _VAR_TYPE_OPT_OFFSET:
        return VariableType(var_type.value - _VAR_TYPE_OPT_OFFSET)

    return var_type


class _OverrideTimeout:
    _ser: serial.Serial
    _old_timeout: int
    _new_timeout: int

    def __init__(self, ser: serial.Serial, timeout: int):
        self._ser = ser
        self._new_timeout = timeout

    def __enter__(self):
        self._old_timeout = self._ser.timeout
        self._ser.timeout = self._new_timeout

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._ser.timeout = self._old_timeout


class Master:
    addr: int
    ser: serial.Serial
    logger: logging.Logger

    _enable_encryption: bool
    _enable_logging: bool

    _log_msgs: dict[int, LogMessageDefinition]

    def __init__(self, addr: int, ser: serial.Serial, logger: logging.Logger, enable_encryption: bool = False):
        self.addr = addr
        self.ser = ser
        self.logger = logger
        self._enable_encryption = enable_encryption
        self._enable_logging = True

        self._log_msgs = dict()

    def _add_log_messages(self, msgs: list[LogMessageDefinition]):
        for msg_def in msgs:
            self._log_msgs[msg_def.code] = msg_def

    def set_logging_enabled(self, enable: bool):
        self._enable_logging = enable

    def override_timeout(self, timeout: int) -> _OverrideTimeout:
        return _OverrideTimeout(self.ser, timeout)

    def poll(self) -> Command:
        return self._request(POLL_REQ, ACK, [])

    def reboot(self) -> Command:
        return self._request(REBOOT_REQ, ACK, [])

    def jump_to_bootloader(self) -> Command:
        return self._request(JUMP_TO_BOOTLOADER_REQ, ACK, [])

    def get_uid(self) -> Command:
        return self._request(GET_UID_REQ, GET_UID_RES, [])

    def get_firmware_version(self) -> Command:
        return self._request(GET_FIRMWARE_VERSION_REQ, GET_FIRMWARE_VERSION_RES, [])

    def _request(self, req: CommandDefinition, res: CommandDefinition,
                 vars: list[int | str], wait_for_logs_after_response=False) -> Command:
        data = req.encode(vars, self.addr, self._enable_encryption)

        hr = req.human_readable(vars, self.addr)

        if self._enable_logging:
            logging.debug(f'> {hr}')
        # logging.debug(f'> "{data.encode("unicode_escape").decode("utf-8")}"')

        self.ser.write(bytes(data, 'ascii'))

        # Wait for response
        response_bytes = self.ser.readline()
        response_str = response_bytes.decode('ascii')
        response = res.decode(response_str, self._enable_encryption)

        while isinstance(response, RawLogMessage):
            log_id = int(response.raw_vars[0], 16)
            if log_id in self._log_msgs:
                decoded = self._log_msgs[log_id].decode(response_str, self._enable_encryption)
                logging.debug(f'  LOG({decoded.addr:x}): {decoded.human_readable()}')
            else:
                logging.debug(f'  LOG({response.addr:x}): Unknown ({log_id:x})')

            response_bytes = self.ser.readline()
            response_str = response_bytes.decode('ascii')
            response = res.decode(response_str, self._enable_encryption)

        return_response = response

        if self._enable_logging:
            logging.debug(f'< {response.human_readable()}')

        if wait_for_logs_after_response:
            old_timeout = self.ser.timeout
            self.ser.timeout = 2

            while True:
                response_bytes = self.ser.readline()
                if len(response_bytes) == 0:
                    break

                response_str = response_bytes.decode('utf8')
                response = res.decode(response_str, self._enable_encryption)
                log_id = int(response.raw_vars[0], 16)
                if log_id in self._log_msgs:
                    decoded = self._log_msgs[log_id].decode(response_str, self._enable_encryption)
                    if self._enable_logging:
                        logging.debug(f'  LOG({decoded.addr:x}): {decoded.human_readable()}')

            self.ser.timeout = old_timeout

        return return_response
