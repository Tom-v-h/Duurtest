import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt5.QtCore import QFile, QTimer
from PyQt5.uic import loadUi
import control_board
import enum
import base64

import logging
import serial
import serial.tools.list_ports as list_ports

import vimbus
import utils

import xml

import Plaatjes_rc

class ResultCode(enum.Enum):
    OK = 0x0001

    WARN_UNIT_NEEDS_CLEANING = 0xAD00
    WARN_UNIT_ALMOST_EMPTY = 0xAD01

    BL_UPDATE_COMPLETE = 0xB001

    DISP_UNIT_CALIB_UPDATE_COMPLETE = 0xB002

    ERR_BL_NO_UPDATE_IN_PROGRESS = 0xBE00
    ERR_BL_INVALID_UPDATE_TYPE = 0xBE01
    ERR_BL_UPDATE_IN_PROGRESS = 0xBE02

    ERR_BL_DATA_SIZE_MISMATCH = 0xBE10
    ERR_BL_DATA_NOT_VALID_BASE64 = 0xBE11
    ERR_BL_DATA_CRC_MISMATCH = 0xBE12

    ERR_BL_WRITE_TO_WRONG_ADDR = 0xBE20
    ERR_BL_WRITE_WILL_OVERRUN = 0xBE21
    ERR_BL_FAILED_TO_ERASE_FLASH = 0xBE22
    ERR_BL_FAILED_TO_WRITE_FLASH = 0xBE23
    ERR_BL_FLASH_CRC_MISMATCH = 0xBE24

    ERR_BL_APP_CRC_INVALID = 0xBEA0
    ERR_BL_APP_CRC_STATE_INVALID = 0xBEA1
    ERR_BL_APP_RESET_HANDLER_ADDR_INVALID = 0xBEA2
    ERR_BL_APP_INITIAL_STACK_PTR_INVALID = 0xBEA3
    ERR_BL_APP_UNKNOWN_FAULT = 0xBEAF

    ERR_BL_INTERNAL = 0xBEFF

    ERR_TABLES_NOT_LOADED = 0xE100
    ERR_COLOR_NOT_FOUND = 0xE101
    ERR_NO_ADDRESS_FOR_COLOR = 0xE102
    ERR_COLOR_NAME_EMPTY = 0xE103
    ERR_NAME_ALREADY_IN_USE = 0xE104

    ERR_UNIT_NOT_ACTIVE = 0xE200
    ERR_UNIT_POLL_TIMEOUT = 0xE201
    ERR_NO_UNITS_ACTIVE = 0xE202
    ERR_UNIT_PREPARE_CMD_TIMEOUT = 0xE210
    ERR_UNIT_PREPARE_CMD_FAILED = 0xE211
    ERR_UNIT_DISPENSE_CMD_TIMEOUT = 0xE212
    ERR_UNIT_DISPENSE_CMD_FAILED = 0xE213
    ERR_UNIT_CANCEL_CMD_TIMEOUT = 0xE214
    ERR_UNIT_GET_DISP_UID_TIMEOUT = 0xE215
    ERR_UNIT_GET_DISP_UID_FAILED = 0xE216
    ERR_UNIT_GET_DISP_UID_MALFORMED = 0xE217
    ERR_UNIT_GET_FW_VER_TIMEOUT = 0xE218
    ERR_UNIT_GET_FW_VER_FAILED = 0xE219
    ERR_UNIT_GET_CAL_VER_TIMEOUT = 0xE21A
    ERR_UNIT_GET_CAL_VER_FAILED = 0xE21B

    ERR_READ_FILL_LVLS_FAILED = 0xE300
    ERR_WRITE_FILL_LVLS_FAILED = 0xE301
    ERR_WRITE_ALIASES_FAILED = 0xE302
    ERR_WRITE_MACHINE_CONFIG_FAILED = 0xE303

    ERR_NO_FILL_LVL_FOUND_FOR_COLOR = 0xE400
    ERR_FILL_LVL_UPDATE_FAILED = 0xE401
    ERR_FILL_LVL_OUT_OF_BOUNDS = 0xE402

    ERR_DUU_BINARY_IMAGE_SLAVE_SIZE_MISMATCH = 0xE500
    ERR_DUU_BLOCK_SIZE_NOT_DIVISOR_OF_BINARY_SIZE = 0xE501
    ERR_DUU_UNIT_IN_INVALID_STATE = 0xE502
    ERR_DUU_ENTER_UPDATE_MODE_TIMEOUT = 0xE510
    ERR_DUU_START_UPDATE_MODE_TIMEOUT = 0xE511
    ERR_DUU_WRITE_TIMEOUT = 0xE512
    ERR_DUU_FINISH_UPDATE_TIMEOUT = 0xE513
    ERR_DUU_BOOT_TO_APP_TIMEOUT = 0xE514
    ERR_DUU_CANCEL_UPDATE_TIMEOUT = 0xE515
    ERR_DUU_UNIT_NOT_ONLINE = 0xE516
    ERR_DUU_BINARY_INVALID_CRC = 0xE520
    ERR_DUCU_UPDATING_OTHER_UNIT = 0xE530
    ERR_DUCU_NO_UPDATE_IN_PROGRESS = 0xE531
    ERR_DUCU_REQUEST_FOR_WRONG_UNIT = 0xE532
    ERR_DUCU_WRITE_TO_WRONG_OFFSET = 0xE533
    ERR_DUCU_DATA_NOT_VALID_BASE64 = 0xE534
    ERR_DUCU_DATA_LENGTH_MISMATCH = 0xE535
    ERR_DUCU_DATA_CRC_MISMATCH = 0xE536
    ERR_DUU_REQUEST_ERROR = 0xE5FF

    ERR_REQUESTED_AMOUNT_OUT_OF_BOUNDS = 0xED00
    ERR_FILL_LVL_TOO_LOW_TO_DISPENSE = 0xED01
    ERR_NO_UNITS_PREPARED = 0xED02
    ERR_TOO_MANY_UNITS_PREPARED = 0xED03
    ERR_UNIT_NO_48V = 0xED04
    ERR_UNIT_SOL_FAULT = 0xED05
    ERR_UNIT_NO_SOL = 0xED06
    ERR_UNIT_NO_VIBR = 0xED07
    ERR_UNKNOWN_DISP_UNIT_ERROR = 0xEDFF


RESULT_CODE = vimbus.EnumFormat(ResultCode, {
    0x0001: 'Ok',

    0xAD00: 'Warn: Dispenser unit needs cleaning',
    0xAD01: 'Warn: Dispenser unit almost empty',

    0xB001: 'Bootloader: Update Complete',
    0xB002: 'Dispenser Unit Calibration Update complete',

    0xBE00: 'Error: No update in progress',
    0xBE01: 'Error: Invalid update type',
    0xBE02: 'Error: Update already in progress',

    0xBE10: 'Error: Data size doesnt match',
    0xBE11: 'Error: Data is not a valid base 64 string',
    0xBE12: 'Error: Data CRC mismatch',

    0xBE20: 'Error: Write to wrong address',
    0xBE21: 'Error: Write will overrun updating segment',
    0xBE22: 'Error: Failed to erase flash',
    0xBE23: 'Error: Failed to write flash',
    0xBE24: 'Error: CRC of written flash mismatch',

    0xBEFF: 'Error: Internal error',

    0xE100: 'Error: Tables not loaded',
    0xE101: 'Error: Color not found',
    0xE102: 'Error: No address for color',
    0xE103: 'Error: Requested color name is empty',
    0xE104: 'Error: Name already in use',

    0xE200: 'Error: Dispenser unit not active',
    0xE201: 'Error: Dispenser unit poll timeout',
    0xE202: 'Error: No Dispenser Units Active',
    0xE210: 'Error: Dispenser unit prepare command timeout',
    0xE211: 'Error: Dispenser unit prepare command failed',
    0xE212: 'Error: Dispenser unit dispense command timeout',
    0xE213: 'Error: Dispenser unit dispense command failed',
    0xE214: 'Error: Dispenser unit cancel command timeout',
    0xE215: 'Error: Dispenser unit get UID command timeout',
    0xE216: 'Error: Dispenser unit get UID command failed',
    0xE217: 'Error: Dispenser unit get UID command response malformed',
    0xE218: 'Error: Dispenser unit get firmware version command timeout',
    0xE219: 'Error: Dispenser unit get firmware version command failed',
    0xE21A: 'Error: Dispenser unit get calibration version timeout',
    0xE21B: 'Error: Dispenser unit get calibration version failed',

    0xE300: 'Error: Failed to read fail levels',
    0xE301: 'Error: Failed to write fill levels',
    0xE302: 'Error: Failed to write aliases',
    0xE303: 'Error: Failed to write machine settings',

    0xE400: 'Error: No fill level found for color',
    0xE401: 'Error: Failed to update fill level',
    0xE402: 'Error: Provided fill level is out of bounds',

    0xE500: 'Error: Mismatch in binary size and slave indicated binary size',
    0xE501: 'Error: Update block size is not a divisor of binary size',
    0xE502: 'Error: Dispenser Unit in Invalid State',
    0xE510: 'Error: Dispenser Unit Enter update mode timeout',
    0xE511: 'Error: Dispenser Unit Start update timeout',
    0xE512: 'Error: Dispenser Unit Write timeout',
    0xE513: 'Error: Dispenser Unit Finish update timeout',
    0xE514: 'Error: Dispenser Unit Boot to app timeout',
    0xE515: 'Error: Dispenser Unit Cancel update timeout',
    0xE516: 'Error: Dispenser Unit not online',
    0xE520: 'Error: Stored Dispenser Unit binary has invalid CRC',
    0xE530: 'Error: Already updating calibration of other dispenser unit',
    0xE531: 'Error: No Dispenser Unit Calibration update in progress',
    0xE532: 'Error: Dispenser Unit Calibration update request for wrong unit',
    0xE533: 'Error: Writing to wrong offset',
    0xE534: 'Error: Dispenser Unit Calibration data is not valid base-64',
    0xE535: 'Error: Dispenser Unit Calibration data length mismatch',
    0xE536: 'Error: Dispenser Unit Calibration data CRC mismatch',
    0xE5FF: 'Error: Dispenser Unit unknown request error',

    0xED00: 'Error: Requested amount is out of bounds',
    0xED01: 'Error: Fill level too low to dispense',
    0xED02: 'Error: No units prepared for dispense',
    0xED03: 'Error: Too many units prepared',
    0xED04: 'Error: Dispenser unit 48V disconnected',
    0xED05: 'Error: Dispenser unit solenoid fault',
    0xED06: 'Error: Dispenser unit solenoid disconnected',
    0xED07: 'Error: Dispenser unit vibration motor disconnected',
    0xEDFF: 'Error: Unknown dispenser unit error',

    0xEFFF: 'Error: Internal error'
})

# Command definitions
READ_ALIAS_NAME_REQ = vimbus.CommandDefinition('ReadAliasName', 0x0BC5, [('color_name', vimbus.VariableType.STRING)])
READ_ALIAS_NAME_RES = vimbus.CommandDefinition('ReadAliasName', 0x0BC5, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE),
    ('alias_name', vimbus.VariableType.STRING)])

CHANGE_ALIAS_NAME_REQ = vimbus.CommandDefinition(
    'ChangeAliasName', 0x0BC4, [('color_name', vimbus.VariableType.STRING), ('alias_name', vimbus.VariableType.STRING)])
CHANGE_ALIAS_NAME_RES = vimbus.CommandDefinition(
    'ChangeAliasName', 0x0BC4, [('result_code', vimbus.VariableType.U16, RESULT_CODE)])

GET_FILL_LEVEL_REQ = vimbus.CommandDefinition(
    'GetFillLevel', 0x0BD3, [('color_or_alias_name', vimbus.VariableType.STRING)])
GET_FILL_LEVEL_RES = vimbus.CommandDefinition(
    'GetFillLevel', 0x0BD3, [
        ('result_code', vimbus.VariableType.U16, RESULT_CODE),
        ('color_or_alias_name', vimbus.VariableType.STRING),
        ('fill_level', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL)])

CORRECT_FILL_LEVEL_REQ = vimbus.CommandDefinition(
    'CorrectFillLevel', 0x0BD4, [
        ('color_or_alias_name', vimbus.VariableType.STRING),
        ('dispenser_address', vimbus.VariableType.U16),
        ('fill_level', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL)])
CORRECT_FILL_LEVEL_RES = vimbus.CommandDefinition(
    'CorrectFillLevel', 0x0BD4, [
        ('color_or_alias_name', vimbus.VariableType.STRING),
        ('result_code', vimbus.VariableType.U16, RESULT_CODE)])

GET_ALL_FILL_LEVELS_REQ = vimbus.CommandDefinition('GetAllFillLevels', 0x0BD6, [])
GET_ALL_FILL_LEVELS_RES = vimbus.CommandDefinition(
    'GetAllFillLevels', 0x0BD6, [
        ('result_code', vimbus.VariableType.U16, RESULT_CODE),
        ('color_or_alias_name_1', vimbus.VariableType.OPT_STRING),
        ('fill_level_1', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_2', vimbus.VariableType.OPT_STRING),
        ('fill_level_2', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_3', vimbus.VariableType.OPT_STRING),
        ('fill_level_3', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_4', vimbus.VariableType.OPT_STRING),
        ('fill_level_4', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_5', vimbus.VariableType.OPT_STRING),
        ('fill_level_5', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_6', vimbus.VariableType.OPT_STRING),
        ('fill_level_6', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_7', vimbus.VariableType.OPT_STRING),
        ('fill_level_7', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_8', vimbus.VariableType.OPT_STRING),
        ('fill_level_8', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_9', vimbus.VariableType.OPT_STRING),
        ('fill_level_9', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_10', vimbus.VariableType.OPT_STRING),
        ('fill_level_10', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_11', vimbus.VariableType.OPT_STRING),
        ('fill_level_11', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_12', vimbus.VariableType.OPT_STRING),
        ('fill_level_12', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_13', vimbus.VariableType.OPT_STRING),
        ('fill_level_13', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_14', vimbus.VariableType.OPT_STRING),
        ('fill_level_14', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_15', vimbus.VariableType.OPT_STRING),
        ('fill_level_15', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_16', vimbus.VariableType.OPT_STRING),
        ('fill_level_16', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
    ])

CORRECT_ALL_FILL_LEVELS_REQ = vimbus.CommandDefinition(
    'CorrectAllFillLevels', 0x0BD8, [
        ('color_or_alias_name_1', vimbus.VariableType.OPT_STRING),
        ('fill_level_1', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_2', vimbus.VariableType.OPT_STRING),
        ('fill_level_2', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_3', vimbus.VariableType.OPT_STRING),
        ('fill_level_3', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_4', vimbus.VariableType.OPT_STRING),
        ('fill_level_4', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_5', vimbus.VariableType.OPT_STRING),
        ('fill_level_5', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_6', vimbus.VariableType.OPT_STRING),
        ('fill_level_6', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_7', vimbus.VariableType.OPT_STRING),
        ('fill_level_7', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_8', vimbus.VariableType.OPT_STRING),
        ('fill_level_8', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_9', vimbus.VariableType.OPT_STRING),
        ('fill_level_9', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_10', vimbus.VariableType.OPT_STRING),
        ('fill_level_10', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_11', vimbus.VariableType.OPT_STRING),
        ('fill_level_11', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_12', vimbus.VariableType.OPT_STRING),
        ('fill_level_12', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_13', vimbus.VariableType.OPT_STRING),
        ('fill_level_13', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_14', vimbus.VariableType.OPT_STRING),
        ('fill_level_14', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_15', vimbus.VariableType.OPT_STRING),
        ('fill_level_15', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
        ('color_or_alias_name_16', vimbus.VariableType.OPT_STRING),
        ('fill_level_16', vimbus.VariableType.OPT_U32, vimbus.FormatHint.DECIMAL),
    ])
CORRECT_ALL_FILL_LEVELS_RES = vimbus.CommandDefinition('CorrectAllFillLevels', 0x0BD8, [
    ('error_alias_or_color_name', vimbus.VariableType.STRING),
    ('result_code', vimbus.VariableType.U16, RESULT_CODE)])

GET_DISPENSER_UNIT_CALIBRATION_VERSION_REQ = vimbus.CommandDefinition('GetDispenserUnitCalibrationVersion', 0x0B08, [
    ('dispenser_unit_address', vimbus.VariableType.U16)])
GET_DISPENSER_UNIT_CALIBRATION_VERSION_RES = vimbus.CommandDefinition('GetDispenserUnitCalibrationVersion', 0x0B08, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE),
    ('version', vimbus.VariableType.STRING)])

GET_DISPENSER_UNIT_FIRMWARE_VERSION_REQ = vimbus.CommandDefinition('GetDispenserUnitFirmwareVersion', 0x0BB0, [
    ('dispenser_unit_address', vimbus.VariableType.U16)])
GET_DISPENSER_UNIT_FIRMWARE_VERSION_RES = vimbus.CommandDefinition('GetDispenserUnitFirmwareVersion', 0x0BB0, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE),
    ('version', vimbus.VariableType.STRING)])

GET_DISPENSER_UNIT_BUILD_INFO_REQ = vimbus.CommandDefinition('GetDispenserUnitBuildInfo', 0x0BB6, [
    ('dispenser_unit_address', vimbus.VariableType.U16)
])
GET_DISPENSER_UNIT_BUILD_INFO_RES = vimbus.CommandDefinition('GetDispenserUnitBuildInfo', 0x0BB6, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE),
    ('version', vimbus.VariableType.STRING),
    ('timestamp', vimbus.VariableType.STRING),
    ('git_hash', vimbus.VariableType.STRING),
    ('git_status', vimbus.VariableType.STRING),
    ('build_type', vimbus.VariableType.STRING)
])

GET_DISPENSER_UNIT_STATUSES_REQ = vimbus.CommandDefinition('GetDispenserUnitStatuses', 0x0BD9, [])
GET_DISPENSER_UNIT_STATUSES_RES = vimbus.CommandDefinition('GetDispenserUnitStatuses', 0x0BD9, [
    ('unit1_address', vimbus.VariableType.U16), ('unit1_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit2_address', vimbus.VariableType.U16), ('unit2_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit3_address', vimbus.VariableType.U16), ('unit3_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit4_address', vimbus.VariableType.U16), ('unit4_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit5_address', vimbus.VariableType.U16), ('unit5_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit6_address', vimbus.VariableType.U16), ('unit6_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit7_address', vimbus.VariableType.U16), ('unit7_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit8_address', vimbus.VariableType.U16), ('unit8_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit9_address', vimbus.VariableType.U16), ('unit9_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit10_address', vimbus.VariableType.U16), ('unit10_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit11_address', vimbus.VariableType.U16), ('unit11_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit12_address', vimbus.VariableType.U16), ('unit12_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit13_address', vimbus.VariableType.U16), ('unit13_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit14_address', vimbus.VariableType.U16), ('unit14_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit15_address', vimbus.VariableType.U16), ('unit15_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
    ('unit16_address', vimbus.VariableType.U16), ('unit16_status', vimbus.VariableType.U16, vimbus.SLAVE_STATE),
])

DISPENSE_NL_REQ = vimbus.CommandDefinition('DispenseNl', 0x0BD0, [
    ('color_or_alias_name', vimbus.VariableType.STRING), ('nl', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL)])
DISPENSE_NL_RES = vimbus.CommandDefinition('DispenseNl', 0x0BD0,
                                           [('result_code', vimbus.VariableType.U16, RESULT_CODE)])

DISPENSE_ALL_REQ = vimbus.CommandDefinition('DispenseAll', 0x0BDA, [])
DISPENSE_ALL_RES = vimbus.CommandDefinition('DispenseAll', 0x0BDA, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE), ('dispenser_unit_address', vimbus.VariableType.U16)])

DISPENSE_CANCEL_REQ = vimbus.CommandDefinition('DispenseCancel', 0x0BDC, [])
DISPENSE_CANCEL_RES = vimbus.CommandDefinition('DispenseCancel', 0x0BDC, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE), ('error_address', vimbus.VariableType.U16)])

GET_READY_UNITS_REQ = vimbus.CommandDefinition('GetReadyUnits', 0x0BDF, [])
GET_READY_UNITS_RES = vimbus.CommandDefinition('GetReadyUnits', 0x0BDF, [
    ('ready_units_count', vimbus.VariableType.U8, vimbus.FormatHint.DECIMAL)])

GET_AVAILABLE_UNITS_REQ = vimbus.CommandDefinition('GetAvailableUnits', 0xBD1, [])
GET_AVAILABLE_UNITS_RES = vimbus.CommandDefinition(
    'GetAvailableUnits', 0xBD1, [
        ('color_or_alias_name_1', vimbus.VariableType.OPT_STRING), ('address_1', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_2', vimbus.VariableType.OPT_STRING), ('address_2', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_3', vimbus.VariableType.OPT_STRING), ('address_3', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_4', vimbus.VariableType.OPT_STRING), ('address_4', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_5', vimbus.VariableType.OPT_STRING), ('address_5', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_6', vimbus.VariableType.OPT_STRING), ('address_6', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_7', vimbus.VariableType.OPT_STRING), ('address_7', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_8', vimbus.VariableType.OPT_STRING), ('address_8', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_9', vimbus.VariableType.OPT_STRING), ('address_9', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_10', vimbus.VariableType.OPT_STRING), ('address_10', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_11', vimbus.VariableType.OPT_STRING), ('address_11', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_12', vimbus.VariableType.OPT_STRING), ('address_12', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_13', vimbus.VariableType.OPT_STRING), ('address_13', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_14', vimbus.VariableType.OPT_STRING), ('address_14', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_15', vimbus.VariableType.OPT_STRING), ('address_15', vimbus.VariableType.OPT_U16),
        ('color_or_alias_name_16', vimbus.VariableType.OPT_STRING), ('address_16', vimbus.VariableType.OPT_U16),
    ])

GET_DISPENSER_UNIT_UID_REQ = vimbus.CommandDefinition('GetDispenserUnitUID', 0x0BA3,
                                                      [('address', vimbus.VariableType.U16)])
GET_DISPENSER_UNIT_UID_RES = vimbus.CommandDefinition('GetDispenserUnitUID', 0x0BA3, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE),
    ('uid', vimbus.VariableType.STRING)])

SET_DISPENSER_UNIT_ADDRESS_BY_UID_REQ = vimbus.CommandDefinition('SetDispenserUnitAddressByUIDCode', 0x0BA4, [
    ('uid', vimbus.VariableType.STRING), ('new_address', vimbus.VariableType.U16)])
SET_DISPENSER_UNIT_ADDRESS_BY_UID_RES = vimbus.CommandDefinition('SetDispenserUnitAddressByUIDCode', 0x0BA4, [
    ('result_code', vimbus.VariableType.U8)])

CHANGE_ADDRESS_REQ = vimbus.CommandDefinition('ChangeAddress', 0x0BC6, [('color_name', vimbus.VariableType.STRING),
                                                                        ('new_address', vimbus.VariableType.U16)])
CHANGE_ADDRESS_RES = vimbus.CommandDefinition('ChangeAddress', 0x0BC6, [('result_code', vimbus.VariableType.U8)])

GET_CB_STATUS_REQ = vimbus.CommandDefinition('GetControlBoardStatus', 0x0BA1, [])
GET_CB_STATUS_RES = vimbus.CommandDefinition('GetControlBoardStatus', 0x0BA1, [('status', vimbus.VariableType.STRING)])

GET_HW_NAME_REQ = vimbus.CommandDefinition('GetHardwareName', 0x0BA9, [])
GET_HW_NAME_RES = vimbus.CommandDefinition('GetHardwareName', 0x0BA9, [('hardware_name', vimbus.VariableType.STRING)])

GET_ALL_COLORS_REQ = vimbus.CommandDefinition('GetAllColors', 0x0BC8, [])
GET_ALL_COLORS_RES = vimbus.CommandDefinition(
    'GetAllColors', 0x0BC8, [
        ('color_1', vimbus.VariableType.STRING),
        ('color_2', vimbus.VariableType.STRING),
        ('color_3', vimbus.VariableType.STRING),
        ('color_4', vimbus.VariableType.STRING),
        ('color_5', vimbus.VariableType.STRING),
        ('color_6', vimbus.VariableType.STRING),
        ('color_7', vimbus.VariableType.STRING),
        ('color_8', vimbus.VariableType.STRING),
        ('color_9', vimbus.VariableType.STRING),
        ('color_10', vimbus.VariableType.STRING),
        ('color_11', vimbus.VariableType.STRING),
        ('color_12', vimbus.VariableType.STRING),
        ('color_13', vimbus.VariableType.STRING),
        ('color_14', vimbus.VariableType.STRING),
        ('color_15', vimbus.VariableType.STRING),
        ('color_16', vimbus.VariableType.STRING),
    ])

READ_MACHINE_CONFIG_REQ = vimbus.CommandDefinition('ReadMachineConfiguration', 0x0B04, [])
READ_MACHINE_CONFIG_RES = vimbus.CommandDefinition('ReadMachineConfiguration', 0x0B04, [
    ('machine_name', vimbus.VariableType.STRING),
    ('client_name', vimbus.VariableType.STRING),
    ('machine_number', vimbus.VariableType.U32),
    ('settings_flags', vimbus.VariableType.U32),
    ('algorithm_version', vimbus.VariableType.STRING)
])

WRITE_MACHINE_CONFIG_REQ = vimbus.CommandDefinition('WriteMachineConfiguration', 0x0B05, [
    ('machine_name', vimbus.VariableType.STRING),
    ('client_name', vimbus.VariableType.STRING),
    ('machine_number', vimbus.VariableType.U32),
    ('settings_flags', vimbus.VariableType.U32),
    ('algorithm_version', vimbus.VariableType.STRING)
])
WRITE_MACHINE_CONFIG_RES = vimbus.CommandDefinition('WriteMachineConfiguration', 0x0B05, [
    ('result_code', vimbus.VariableType.U16, RESULT_CODE)])

UPDATE_DISPENSER_UNIT_REQ = vimbus.CommandDefinition('UpdateDispenserUnit', 0x0BB1, [
    ('address', vimbus.VariableType.U16)])
UPDATE_DISPENSER_UNIT_RES = vimbus.CommandDefinition('UpdateDispenserUnit', 0x0BB1, [
    ('result', vimbus.VariableType.U16, RESULT_CODE)])

UPDATE_DISPENSER_UNIT_CALIBRATION_REQ = vimbus.CommandDefinition('UpdateDispenserUnitCalibration', 0x0BB3, [
    ('address', vimbus.VariableType.U16)])
UPDATE_DISPENSER_UNIT_CALIBRATION_RES = vimbus.CommandDefinition('UpdateDispenserUnitCalibration', 0x0BB3, [
    ('result', vimbus.VariableType.U16, RESULT_CODE),
    ('next_offset', vimbus.VariableType.U32),
    ('calibration_size', vimbus.VariableType.U32)])

GET_DISPENSER_UNIT_BINARY_STATUS_REQ = vimbus.CommandDefinition('GetDispenserUnitBinaryStatus', 0x0BB5, [])
GET_DISPENSER_UNIT_BINARY_STATUS_RES = vimbus.CommandDefinition('GetDispenserUnitBinaryStatus', 0x0BB5, [
    ('result', vimbus.VariableType.U16, RESULT_CODE),
    ('crc', vimbus.VariableType.U16),
    ('crc_calc', vimbus.VariableType.U16)])

WRITE_DISPENSER_UNIT_CALIBRATION_REQ = vimbus.CommandDefinition('WriteDispenserUnitCalibration', 0x0BBA, [
    ('address', vimbus.VariableType.U16),
    ('offset', vimbus.VariableType.U32),
    ('size', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
    ('data', vimbus.VariableType.STRING),
    ('crc', vimbus.VariableType.U16)])
WRITE_DISPENSER_UNIT_CALIBRATION_RES = vimbus.CommandDefinition('WriteDispenserUnitCalibration', 0x0BBA, [
    ('result', vimbus.VariableType.U16, RESULT_CODE),
    ('next_offset', vimbus.VariableType.U32)])

CANCEL_UPDATE_DISPENSER_UNIT_CALIBRATION_REQ = vimbus.CommandDefinition('CancelUpdateDispenserUnitCalibration', 0x0BB4,
                                                                        [
                                                                            ('address', vimbus.VariableType.U16)])
CANCEL_UPDATE_DISPENSER_UNIT_CALIBRATION_RES = vimbus.CommandDefinition('CancelUpdateDispenserUnitCalibration', 0x0BB4,
                                                                        [
                                                                            ('result', vimbus.VariableType.U16,
                                                                             RESULT_CODE)])

ENSURE_DISPENSER_UNIT_ONLINE_REQ = vimbus.CommandDefinition('EnsureDispenserUnitOnline', 0x0BAB,
                                                            [('address', vimbus.VariableType.U16)])
ENSURE_DISPENSER_UNIT_ONLINE_RES = vimbus.CommandDefinition('EnsureDispenserUnitOnline', 0x0BAB,
                                                            [('result', vimbus.VariableType.U16, RESULT_CODE)])


CAL_LOOP_SHOTS_REQ = vimbus.CommandDefinition('CalibrationLoopShots', 0x0BA6, [
    ('address', vimbus.VariableType.U16),
    ('disp_time', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
    ('loop_amount', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL),
    ('interval_time', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
    ('vibr_flags', vimbus.VariableType.U8)
])
CAL_LOOP_SHOTS_RES = vimbus.CommandDefinition('CalibrationLoopShots', 0x0BA6, [
    ('result', vimbus.VariableType.U8),
])

CAL_HEAT_UP_REQ = vimbus.CommandDefinition('CalibrationHeatUp', 0x0BA7, [
    ('address', vimbus.VariableType.U16),
    ('temperature', vimbus.VariableType.U8, vimbus.FormatHint.DECIMAL)
])
CAL_HEAT_UP_RES = vimbus.CommandDefinition('CalibrationHeatUp', 0x0BA7, [
    ('result', vimbus.VariableType.U8),
    ('result_msg', vimbus.VariableType.STRING)
])


# Log message definitions
LOG_CB_DISPENSE_PREPARE = vimbus.LogMessageDefinition(
    'DispensePrepareCBParameters', 0x0DC0, [
        ('requested_amount', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('requested_color', vimbus.VariableType.STRING),
        ('disp_unit_flow_factor', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL),
        ('corrected_volume', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('aosp_lut_lower_interp_volume', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('aosp_lut_upper_interp_volume', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('aosp_lut_lower_interp_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('aosp_lut_upper_interp_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('aosp_lut_calculated_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL)
    ])

LOG_DU_DISPENSE_PREPARE = vimbus.LogMessageDefinition(
    'DispensePrepareDUParameters', 0x0DD0, [
        ('requested_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('measured_temperature', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_lower_interp_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_upper_interp_aosp', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_lower_interp_temp', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL),
        ('ot_lut_upper_interp_temp', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL),
        ('ot_lut_lower_temp_lower_aosp_interp_ot', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_upper_temp_lower_aosp_interp_ot', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_lower_temp_upper_aosp_interp_ot', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_upper_temp_upper_aosp_interp_ot', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('ot_lut_calculated_open_time', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL)
    ])


class DispensingState(enum.Enum):
    IDLE = 0x01
    VIBRATING_BEFORE_DISPENSE = 0x10
    DISPENSING = 0x11
    VIBRATING_AFTER_DISPENSE = 0x12
    ERROR = 0xE0


DISPENSING_STATE = vimbus.EnumFormat(DispensingState, {
    0x01: 'Idle',
    0x10: 'Vibrating before dispense',
    0x11: 'Dispensing',
    0x12: 'Vibrating after dispense',
    0xE0: 'Error'
})

LOG_DU_DISPENSING_STATUS = vimbus.LogMessageDefinition(
    'DispensingStatus', 0x0DD1, [
        ('state', vimbus.VariableType.U8, DISPENSING_STATE),
        ('solenoid_opened', vimbus.VariableType.U8, vimbus.FormatHint.BOOLEAN),
        ('time_since_state_change', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('total_dispense_time', vimbus.VariableType.U32, vimbus.FormatHint.DECIMAL),
        ('measured_temperature', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL),
        ('measured_supply_voltage', vimbus.VariableType.U16, vimbus.FormatHint.DECIMAL)
    ])


class DUUpdateStep(enum.Enum):
    ENTER_UPDATE_MODE = 0x0010
    CANCEL_PREVIOUS_UPDATE = 0x0011
    START_UPDATE = 0x0012
    WRITE = 0x0013
    FINISH_UPDATE = 0x0014
    BOOT_TO_APP = 0x0015
    UNKNOWN = 0x00E0


DU_UPDATE_STEP = vimbus.EnumFormat(DUUpdateStep, {
    0x0010: 'Enter Update Mode',
    0x0011: 'Cancel Previous Update',
    0x0012: 'Start Update',
    0x0013: 'Write',
    0x0014: 'Finish Update',
    0x0015: 'Boot To App',
    0x00E0: 'Unknown'
})

LOG_UPDATE_DU_APPLICATION_PROGRESS = vimbus.LogMessageDefinition(
    'UpdateDUApplicationProgress', 0xBDA0, [
        ('step', vimbus.VariableType.U16, DU_UPDATE_STEP),
        ('bytes_written', vimbus.VariableType.U32),
        ('total_bytes', vimbus.VariableType.U32)
    ])

COMMANDS: list[tuple[vimbus.CommandDefinition, vimbus.CommandDefinition]] = [
    (READ_ALIAS_NAME_REQ, READ_ALIAS_NAME_RES),
    (CHANGE_ALIAS_NAME_REQ, CHANGE_ALIAS_NAME_RES),
    (GET_FILL_LEVEL_REQ, GET_FILL_LEVEL_RES),
    (GET_ALL_FILL_LEVELS_REQ, GET_ALL_FILL_LEVELS_RES),
    (CORRECT_FILL_LEVEL_REQ, CORRECT_FILL_LEVEL_RES),
    (CORRECT_ALL_FILL_LEVELS_REQ, CORRECT_ALL_FILL_LEVELS_RES),
    (GET_DISPENSER_UNIT_CALIBRATION_VERSION_REQ, GET_DISPENSER_UNIT_CALIBRATION_VERSION_RES),
    (GET_DISPENSER_UNIT_FIRMWARE_VERSION_REQ, GET_DISPENSER_UNIT_FIRMWARE_VERSION_RES),
    (DISPENSE_NL_REQ, DISPENSE_NL_RES),
    (DISPENSE_ALL_REQ, DISPENSE_ALL_RES),
    (DISPENSE_CANCEL_REQ, DISPENSE_CANCEL_RES),
    (GET_READY_UNITS_REQ, GET_READY_UNITS_RES),
]

LOG_MSGS: list[vimbus.LogMessageDefinition] = [
    LOG_CB_DISPENSE_PREPARE,
    LOG_DU_DISPENSE_PREPARE,
    LOG_DU_DISPENSING_STATUS,
    LOG_UPDATE_DU_APPLICATION_PROGRESS,
]


def find_cb_serial() -> str:
    ports = list_ports.comports()

    # Attempt to find VC1000 Control Board
    cb_candidates = [p for p in ports if p.manufacturer == 'FTDI']
    if len(cb_candidates) == 1:
        return cb_candidates[0].device
    elif len(cb_candidates) > 1:
        raise Exception(f'Multiple serial port candidates found: {", ".join([c.description for c in cb_candidates])}')

    # Attempt to find STM32 Nucleo (integrated ST-Link)
    stlink_candidates = [p for p in ports if p.manufacturer == 'STMicroelectronics' and 'STLink' in p.description]

    # Check if we have exactly one candidate
    if len(stlink_candidates) == 0:
        raise Exception('No serial port candidate found')
    elif len(stlink_candidates) > 1:
        raise Exception(
            f'Multiple serial port candidates found: {", ".join([c.description for c in stlink_candidates])}')

    return stlink_candidates[0].device


class ControlBoard(vimbus.Master):
    def __init__(self, address: int, ser: serial.Serial, logger: logging.Logger, enable_encryption: bool = False):
        super().__init__(address, ser, logger, enable_encryption)
        self._add_log_messages(LOG_MSGS)

    def get_status(self) -> vimbus.Command:
        return self._request(GET_CB_STATUS_REQ, GET_CB_STATUS_RES, [])
    
    def get_fill_level(self, name: str) -> vimbus.Command:
        return self._request(GET_FILL_LEVEL_REQ, GET_FILL_LEVEL_RES, [name])

    def get_all_fill_levels(self) -> vimbus.Command:
        return self._request(GET_ALL_FILL_LEVELS_REQ, GET_ALL_FILL_LEVELS_RES, [])

    def correct_fill_level(self, name: str, address: int, fill_level: int) -> vimbus.Command:
        return self._request(CORRECT_FILL_LEVEL_REQ, CORRECT_FILL_LEVEL_RES,
                             [name, address, fill_level])

    def correct_all_fill_levels(self, args: list[str | int]) -> vimbus.Command:
        return self._request(CORRECT_ALL_FILL_LEVELS_REQ, CORRECT_ALL_FILL_LEVELS_RES, args)

    def correct_all_fill_levels_nack(self, args: list[str | int]) -> vimbus.Command:
        return self._request(CORRECT_ALL_FILL_LEVELS_REQ, vimbus.NACK, args)

    def dispense_nl(self, color_or_alias_name: str, nl: int) -> vimbus.Command:
        return self._request(DISPENSE_NL_REQ, DISPENSE_NL_RES, [color_or_alias_name, nl])

    def dispense_nl_nack(self, color_or_alias_name: str, nl: int) -> vimbus.Command:
        return self._request(DISPENSE_NL_REQ, vimbus.NACK, [color_or_alias_name, nl])

    def dispense_all(self) -> vimbus.Command:
        return self._request(DISPENSE_ALL_REQ, DISPENSE_ALL_RES, [], True)

    def dispense_cancel(self) -> vimbus.Command:
        return self._request(DISPENSE_CANCEL_REQ, DISPENSE_CANCEL_RES, [])

    def get_ready_units(self) -> vimbus.Command:
        return self._request(GET_READY_UNITS_REQ, GET_READY_UNITS_RES, [])

    
def errorMessageBox(e):
    text = str(e)
    print(text)
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Information)
    msg.setText(text)
    msg.setWindowTitle("Dialog")
    msg.setStandardButtons(QMessageBox.Ok)

    # Show the message box
    msg.exec_()

class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Load the UI file
        ui_file = QFile("V1.ui")
        ui_file.open(QFile.ReadOnly)
        loadUi(ui_file, self)
        ui_file.close()
        try:
            adress = find_cb_serial()
        except Exception as e:
            print("An unexpected error occurred:", e)
        self.machine = ControlBoard(0x0002, serial.Serial(adress, baudrate=19200), logging.getLogger(), True)
        self.machine.poll()
        # Connect signals and slots
        self.pushButton.clicked.connect(self.showMessageBox)

    def showMessageBox(self):
        # Show a message box when the button is clicked
        self.pushButton.setDisabled(True)
        QTimer.singleShot(12000, lambda: self.pushButton.setEnabled(True))
            
        dispenseValue = 5_000_000
        total = int(self.dial.value()) + int(self.dial_2.value()) + int(self.dial_3.value()) +int(self.dial_4.value())
        
        red = int(dispenseValue * int(self.dial.value()) / total)
        white = int(dispenseValue * int(self.dial_2.value())/ total)
        blue = int(dispenseValue * int(self.dial_3.value())/ total)
        orange = int(dispenseValue * int(self.dial_4.value())/ total)
        total = red + white + blue + orange
        print(red, white, blue, orange, total)
        try:
            if (red > 0):
                self.machine.dispense_nl('red', red)
            if (white>0):
                self.machine.dispense_nl('white', white)
            if (blue > 0):
                self.machine.dispense_nl('blue', blue)
            if (orange > 0):
                self.machine.dispense_nl('orange', orange)
            self.machine.dispense_all()
        except Exception as e:
            print("An unexpected error occurred:", e)

        finally:
            self.dial.setValue(25)
            self.dial_2.setValue(25)
            self.dial_3.setValue(25)
            self.dial_4.setValue(25)
        
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MyWindow()
    window.show()
    sys.exit(app.exec_())
