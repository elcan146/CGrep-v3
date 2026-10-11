"""
SDRplay Radio Wrapper
=====================

Control SDRplay software-defined radio hardware from Python.

Example:
    with SDRPlayRadio() as radio:
        print(radio.version)
        print(radio.devices)

IMPORTANT - Callback Threading Model:
    Stream and event callbacks are invoked from internal API threads,
    NOT from the Python main thread. This means:

    1. Do NOT perform blocking operations in callbacks (file I/O, network, etc.)
    2. Do NOT call SDRplay API functions from within callbacks
    3. Use thread-safe data structures (queue.Queue, collections.deque) to pass
       data from callbacks to your main processing thread
    4. Keep callback execution time minimal to avoid buffer overflows
    5. Exceptions in callbacks are caught and logged but do not propagate

    Example of thread-safe sample handling:

        import queue
        sample_queue = queue.Queue()

        def on_samples(xi, xq, params, num_samples, reset, ctx):
            # Just copy data to queue, process elsewhere
            sample_queue.put((xi[:num_samples], xq[:num_samples]))

        radio.start(on_samples)
        while streaming:
            xi, xq = sample_queue.get()  # Process in main thread
"""

import ctypes
import ctypes.util
import os
import platform
import sys
import time
import traceback
from enum import IntEnum


# =============================================================================
# CONSTANTS
# =============================================================================

# API version this wrapper targets
SDRPLAY_API_VERSION = 3.15

SDRPLAY_MAX_DEVICES = 16
SDRPLAY_MAX_TUNERS_PER_DEVICE = 2
SDRPLAY_MAX_SER_NO_LEN = 64
SDRPLAY_MAX_ROOT_NM_LEN = 32
MAX_BB_GR = 59  # Maximum baseband gain reduction

# Device IDs (hwVer values)
SDRPLAY_RSP1_ID = 1
SDRPLAY_RSP1A_ID = 255
SDRPLAY_RSP2_ID = 2
SDRPLAY_RSPduo_ID = 3
SDRPLAY_RSPdx_ID = 4
SDRPLAY_RSP1B_ID = 6
SDRPLAY_RSPdxR2_ID = 7

# Human-readable device names
DEVICE_NAMES = {
    SDRPLAY_RSP1_ID: "RSP1",
    SDRPLAY_RSP1A_ID: "RSP1A",
    SDRPLAY_RSP1B_ID: "RSP1B",
    SDRPLAY_RSP2_ID: "RSP2",
    SDRPLAY_RSPduo_ID: "RSPduo",
    SDRPLAY_RSPdx_ID: "RSPdx",
    SDRPLAY_RSPdxR2_ID: "RSPdxR2",
}

# =============================================================================
# LNA STATE COUNTS PER DEVICE AND FREQUENCY BAND
# =============================================================================
# Valid LNA states are 0 to (count - 1). These values are from API spec pg 38-39.
# Use get_max_lna_state() method to get the correct value for current frequency.

# RSP1 (original) - 4 LNA states for all bands
RSP1_NUM_LNA_STATES = 4

# RSP1A/RSP1B - varies by frequency band
RSP1A_NUM_LNA_STATES = 10  # 60 MHz - 1000 MHz (default)
RSP1A_NUM_LNA_STATES_AM = 7  # 0 - 60 MHz (AM band)
RSP1A_NUM_LNA_STATES_LBAND = 9  # 1000 MHz - 2000 MHz (L-band)

# RSP2 - varies by frequency band and antenna
RSP2_NUM_LNA_STATES = 9  # 420 MHz - 1000 MHz Antenna A (default)
RSP2_NUM_LNA_STATES_AM = 6  # 0 - 60 MHz (AM band)
RSP2_NUM_LNA_STATES_VHF = 9  # 60 MHz - 420 MHz
RSP2_NUM_LNA_STATES_LBAND = 6  # 1000 MHz - 2000 MHz
RSP2_NUM_LNA_STATES_HIZ = 5  # Hi-Z port (0 - 60 MHz)

# RSPduo - varies by frequency band and antenna port
RSPDUO_NUM_LNA_STATES = 10  # 60 MHz - 1000 MHz (default)
RSPDUO_NUM_LNA_STATES_AM = 7  # 0 - 60 MHz (AM band)
RSPDUO_NUM_LNA_STATES_LBAND = 9  # 1000 MHz - 2000 MHz
RSPDUO_NUM_LNA_STATES_HIZ = 5  # Hi-Z port (0 - 60 MHz)

# RSPdx/RSPdxR2 - varies by frequency band (HDR mode has different values)
RSPDX_NUM_LNA_STATES = 28  # 200 MHz - 1000 MHz (default)
RSPDX_NUM_LNA_STATES_AM = 22  # 0 - 12 MHz
RSPDX_NUM_LNA_STATES_VLF = 19  # 12 MHz - 50 MHz
RSPDX_NUM_LNA_STATES_VHF = 27  # 50 MHz - 200 MHz
RSPDX_NUM_LNA_STATES_LBAND = 21  # 1000 MHz - 2000 MHz
RSPDX_NUM_LNA_STATES_HDR = 22  # HDR mode (any freq < 2 MHz)


# =============================================================================
# DOCUMENTATION: Device Parameter Mappings
# =============================================================================
# The SDRplay API uses shared parameter structures for similar devices:
#
# RSP1B → Uses RSP1A parameters
#   - RSP1B is functionally identical to RSP1A
#   - Use rsp1aParams for device params, rsp1aTunerParams for tuner params
#   - All RSP1A update reasons work with RSP1B
#
# RSPdxR2 → Uses RSPdx parameters
#   - RSPdxR2 is the updated version of RSPdx
#   - Use rspDxParams for device params, rspDxTunerParams for tuner params
#   - All RSPdx update reasons work with RSPdxR2


# =============================================================================
# DOCUMENTATION: Valid IF Mode and Bandwidth Combinations
# =============================================================================
# The API has specific requirements for IF mode and bandwidth combinations.
#
# Zero-IF Mode (If.ZERO):
#   - Supported bandwidths: All (200kHz to 8MHz)
#   - Supported sample rates: 2MHz to 10.66MHz
#   - Recommended for most applications
#
# Low-IF Modes (If.IF_0_450, IF_1_620, IF_2_048):
#   - Supported sample rates: 6MHz, 7MHz, or 8MHz ONLY
#   - Each IF mode has a specific bandwidth requirement:
#
#     IF_0_450 (450 kHz IF):
#       - Bandwidth: Bw.BW_0_200, BW_0_300, BW_0_600
#       - Sample rate: 6MHz, 7MHz, 8MHz
#
#     IF_1_620 (1.62 MHz IF):
#       - Bandwidth: Bw.BW_1_536
#       - Sample rate: 6MHz, 7MHz, 8MHz
#
#     IF_2_048 (2.048 MHz IF):
#       - Bandwidth: Bw.BW_1_536, BW_5_000, BW_6_000, BW_7_000, BW_8_000
#       - Sample rate: 6MHz, 7MHz, 8MHz
#
# (see VALID_IF_BW_COMBINATIONS dict after enumerations section)


# =============================================================================
# DOCUMENTATION: RSPduo Operating Modes
# =============================================================================
# The RSPduo has unique multi-mode capabilities:
#
# SINGLE_TUNER Mode (RspDuoMode.SINGLE_TUNER = 1):
#   - Uses one tuner (A or B) at up to 10.66 MHz sample rate
#   - Full frequency range (1 kHz to 2 GHz)
#   - Tuner A has Hi-Z port access
#   - select_device(serial, tuner=TunerSelect.A) or TunerSelect.B
#
# DUAL_TUNER Mode (RspDuoMode.DUAL_TUNER = 2):
#   - Uses both tuners simultaneously
#   - Sample rate limited to 6 MHz or 8 MHz (shared ADC)
#   - Each tuner can be tuned independently
#   - select_device(serial, tuner=TunerSelect.BOTH)
#
# MASTER Mode (RspDuoMode.MASTER = 4):
#   - Device acts as master for another RSPduo application
#   - Controls the shared oscillator
#   - Slave can attach/detach dynamically
#
# SLAVE Mode (RspDuoMode.SLAVE = 8):
#   - Device acts as slave to a master RSPduo
#   - Uses oscillator from master device
#   - Must match master's sample rate
#
# RSPduo Tuner Port Notes:
#   - Tuner A (Port 1): 1 kHz - 2 GHz, includes Hi-Z input
#   - Tuner B (Port 2): 1 kHz - 2 GHz, standard 50Ω input
#   - Hi-Z input only available on Tuner A via AM port selection


# =============================================================================
# ENUMERATIONS
# =============================================================================


class ApiError(IntEnum):
    """API error codes returned by all functions."""

    SUCCESS = 0
    FAIL = 1
    INVALID_PARAM = 2
    OUT_OF_RANGE = 3
    GAIN_UPDATE_ERROR = 4
    RF_UPDATE_ERROR = 5
    FS_UPDATE_ERROR = 6
    HW_ERROR = 7
    ALIASING_ERROR = 8
    ALREADY_INITIALISED = 9
    NOT_INITIALISED = 10
    NOT_ENABLED = 11
    HW_VER_ERROR = 12
    OUT_OF_MEM_ERROR = 13
    SERVICE_NOT_RESPONDING = 14
    START_PENDING = 15
    STOP_PENDING = 16
    INVALID_MODE = 17
    FAILED_VERIFICATION1 = 18
    FAILED_VERIFICATION2 = 19
    FAILED_VERIFICATION3 = 20
    FAILED_VERIFICATION4 = 21
    FAILED_VERIFICATION5 = 22
    FAILED_VERIFICATION6 = 23
    INVALID_SERVICE_VERSION = 24


class DbgLvl(IntEnum):
    """Debug logging levels."""

    DISABLE = 0
    VERBOSE = 1
    WARNING = 2
    ERROR = 3
    MESSAGE = 4


class Bw(IntEnum):
    """IF bandwidth in kHz."""

    UNDEFINED = 0
    BW_0_200 = 200
    BW_0_300 = 300
    BW_0_600 = 600
    BW_1_536 = 1536
    BW_5_000 = 5000
    BW_6_000 = 6000
    BW_7_000 = 7000
    BW_8_000 = 8000


class If(IntEnum):
    """IF mode frequency in kHz."""

    UNDEFINED = -1
    ZERO = 0
    IF_0_450 = 450
    IF_1_620 = 1620
    IF_2_048 = 2048


class LoMode(IntEnum):
    """LO mode selection."""

    UNDEFINED = 0
    AUTO = 1
    LO_120MHZ = 2
    LO_144MHZ = 3
    LO_168MHZ = 4


class MinGainReduction(IntEnum):
    """Minimum gain reduction mode."""

    EXTENDED_MIN_GR = 0
    NORMAL_MIN_GR = 20


class TunerSelect(IntEnum):
    """Tuner selection for dual-tuner devices."""

    NEITHER = 0
    A = 1
    B = 2
    BOTH = 3


class TransferMode(IntEnum):
    """USB transfer mode."""

    ISOCH = 0
    BULK = 1


class AgcControl(IntEnum):
    """AGC control mode."""

    DISABLE = 0
    AGC_100HZ = 1
    AGC_50HZ = 2
    AGC_5HZ = 3
    CTRL_EN = 4


class AdsbMode(IntEnum):
    """ADS-B mode configuration."""

    DECIMATION = 0
    NO_DECIMATION_LOWPASS = 1
    NO_DECIMATION_BANDPASS_2MHZ = 2
    NO_DECIMATION_BANDPASS_3MHZ = 3


class RspDuoMode(IntEnum):
    """RSPduo operating mode."""

    UNKNOWN = 0
    SINGLE_TUNER = 1
    DUAL_TUNER = 2
    MASTER = 4
    SLAVE = 8


class Rsp2AntennaSelect(IntEnum):
    """RSP2 antenna selection."""

    ANTENNA_A = 5
    ANTENNA_B = 6


class Rsp2AmPortSelect(IntEnum):
    """RSP2 AM port selection."""

    AMPORT_1 = 1
    AMPORT_2 = 0


class RspDuoAmPortSelect(IntEnum):
    """RSPduo AM port selection."""

    AMPORT_1 = 1
    AMPORT_2 = 0


class RspDxAntennaSelect(IntEnum):
    """RSPdx antenna selection."""

    ANTENNA_A = 0
    ANTENNA_B = 1
    ANTENNA_C = 2


class RspDxHdrModeBw(IntEnum):
    """RSPdx HDR mode bandwidth."""

    BW_0_200 = 0
    BW_0_500 = 1
    BW_1_200 = 2
    BW_1_700 = 3


class PowerOverloadCbEventId(IntEnum):
    """Power overload callback event type."""

    OVERLOAD_DETECTED = 0
    OVERLOAD_CORRECTED = 1


class RspDuoModeCbEventId(IntEnum):
    """RSPduo mode change callback event type."""

    MASTER_INITIALISED = 0
    SLAVE_ATTACHED = 1
    SLAVE_DETACHED = 2
    SLAVE_INITIALISED = 3
    SLAVE_UNINITIALISED = 4
    MASTER_DLL_DISAPPEARED = 5
    SLAVE_DLL_DISAPPEARED = 6


class Event(IntEnum):
    """Event callback types."""

    GAIN_CHANGE = 0
    POWER_OVERLOAD_CHANGE = 1
    DEVICE_REMOVED = 2
    RSPDUO_MODE_CHANGE = 3
    DEVICE_FAILURE = 4


class ReasonForUpdate(IntEnum):
    """Reasons for calling sdrplay_api_Update (bitmask values)."""

    NONE = 0x00000000
    DEV_FS = 0x00000001
    DEV_PPM = 0x00000002
    DEV_SYNC_UPDATE = 0x00000004
    DEV_RESET_FLAGS = 0x00000008
    RSP1A_BIAS_T_CONTROL = 0x00000010
    RSP1A_RF_NOTCH_CONTROL = 0x00000020
    RSP1A_RF_DAB_NOTCH_CONTROL = 0x00000040
    RSP2_BIAS_T_CONTROL = 0x00000080
    RSP2_AM_PORT_SELECT = 0x00000100
    RSP2_ANTENNA_CONTROL = 0x00000200
    RSP2_RF_NOTCH_CONTROL = 0x00000400
    RSP2_EXT_REF_CONTROL = 0x00000800
    RSPDUO_EXT_REF_CONTROL = 0x00001000
    TUNER_GR = 0x00008000
    TUNER_GR_LIMITS = 0x00010000
    TUNER_FRF = 0x00020000
    TUNER_BW_TYPE = 0x00040000
    TUNER_IF_TYPE = 0x00080000
    TUNER_DC_OFFSET = 0x00100000
    TUNER_LO_MODE = 0x00200000
    CTRL_DC_OFFSET_IQ_IMBALANCE = 0x00400000
    CTRL_DECIMATION = 0x00800000
    CTRL_AGC = 0x01000000
    CTRL_ADSB_MODE = 0x02000000
    CTRL_OVERLOAD_MSG_ACK = 0x04000000
    RSPDUO_BIAS_T_CONTROL = 0x08000000
    RSPDUO_AM_PORT_SELECT = 0x10000000
    RSPDUO_TUNER1_AM_NOTCH_CONTROL = 0x20000000
    RSPDUO_RF_NOTCH_CONTROL = 0x40000000
    RSPDUO_RF_DAB_NOTCH_CONTROL = 0x80000000


class ReasonForUpdateExt1(IntEnum):
    """Extended reasons for calling sdrplay_api_Update (bitmask values)."""

    NONE = 0x00000000
    RSPDX_HDR_ENABLE = 0x00000001
    RSPDX_BIAS_T_CONTROL = 0x00000002
    RSPDX_ANTENNA_CONTROL = 0x00000004
    RSPDX_RF_NOTCH_CONTROL = 0x00000008
    RSPDX_RF_DAB_NOTCH_CONTROL = 0x00000010
    RSPDX_HDR_BW = 0x00000020
    RSPDUO_RESET_SLAVE_FLAGS = 0x00000040


# Valid IF mode and bandwidth combinations (see documentation above)
VALID_IF_BW_COMBINATIONS = {
    If.ZERO: [
        Bw.BW_0_200,
        Bw.BW_0_300,
        Bw.BW_0_600,
        Bw.BW_1_536,
        Bw.BW_5_000,
        Bw.BW_6_000,
        Bw.BW_7_000,
        Bw.BW_8_000,
    ],
    If.IF_0_450: [Bw.BW_0_200, Bw.BW_0_300, Bw.BW_0_600],
    If.IF_1_620: [Bw.BW_1_536],
    If.IF_2_048: [Bw.BW_1_536, Bw.BW_5_000, Bw.BW_6_000, Bw.BW_7_000, Bw.BW_8_000],
}


# =============================================================================
# ERROR HANDLING
# =============================================================================


class SDRPlayError(Exception):
    """
    Raised when something goes wrong with the radio.

    Example:
        try:
            radio = SDRPlayRadio()
        except SDRPlayError as error:
            print(f"Problem: {error}")
    """

    pass


# =============================================================================
# C LIBRARY TYPES
# =============================================================================
# The SDRplay API is written in C. To call C code from Python, we need to
# describe the data types that C uses. This section defines those types.

# Basic C types
CInt = ctypes.c_int
CUInt = ctypes.c_uint
CUShort = ctypes.c_ushort
CUChar = ctypes.c_ubyte
CFloat = ctypes.c_float
CDouble = ctypes.c_double
CChar = ctypes.c_char
CCharPointer = ctypes.c_char_p
CVoidPointer = ctypes.c_void_p
CShortPointer = ctypes.POINTER(ctypes.c_short)


# =============================================================================
# STRUCTURES - Device Enumeration
# =============================================================================


class DeviceT(ctypes.Structure):
    """Device enumeration structure (sdrplay_api_DeviceT)."""

    _fields_ = [
        ("SerNo", CChar * SDRPLAY_MAX_SER_NO_LEN),
        ("hwVer", CUChar),
        ("tuner", CInt),
        ("rspDuoMode", CInt),
        ("valid", CUChar),
        ("rspDuoSampleFreq", CDouble),
        ("dev", CVoidPointer),
    ]


class ErrorInfoT(ctypes.Structure):
    """Extended error message structure."""

    _fields_ = [
        ("file", CChar * 256),
        ("function", CChar * 256),
        ("line", CInt),
        ("message", CChar * 1024),
    ]


# =============================================================================
# STRUCTURES - Tuner Parameters
# =============================================================================


class GainValuesT(ctypes.Structure):
    """Current gain values (output from API)."""

    _fields_ = [
        ("curr", CFloat),
        ("max", CFloat),
        ("min", CFloat),
    ]


class GainT(ctypes.Structure):
    """Gain setting parameters."""

    _fields_ = [
        ("gRdB", CInt),
        ("LNAstate", CUChar),
        ("syncUpdate", CUChar),
        ("minGr", CInt),
        ("gainVals", GainValuesT),
    ]


class RfFreqT(ctypes.Structure):
    """RF frequency parameters."""

    _fields_ = [
        ("rfHz", CDouble),
        ("syncUpdate", CUChar),
    ]


class DcOffsetTunerT(ctypes.Structure):
    """DC calibration parameters."""

    _fields_ = [
        ("dcCal", CUChar),
        ("speedUp", CUChar),
        ("trackTime", CInt),
        ("refreshRateTime", CInt),
    ]


class TunerParamsT(ctypes.Structure):
    """Tuner parameters."""

    _fields_ = [
        ("bwType", CInt),
        ("ifType", CInt),
        ("loMode", CInt),
        ("gain", GainT),
        ("rfFreq", RfFreqT),
        ("dcOffsetTuner", DcOffsetTunerT),
    ]


# =============================================================================
# STRUCTURES - Control Parameters
# =============================================================================


class DcOffsetT(ctypes.Structure):
    """DC offset control parameters."""

    _fields_ = [
        ("DCenable", CUChar),
        ("IQenable", CUChar),
    ]


class DecimationT(ctypes.Structure):
    """Decimation control parameters."""

    _fields_ = [
        ("enable", CUChar),
        ("decimationFactor", CUChar),
        ("wideBandSignal", CUChar),
    ]


class AgcT(ctypes.Structure):
    """AGC control parameters."""

    _fields_ = [
        ("enable", CInt),
        ("setPoint_dBfs", CInt),
        ("attack_ms", CUShort),
        ("decay_ms", CUShort),
        ("decay_delay_ms", CUShort),
        ("decay_threshold_dB", CUShort),
        ("syncUpdate", CInt),
    ]


class ControlParamsT(ctypes.Structure):
    """Control parameters."""

    _fields_ = [
        ("dcOffset", DcOffsetT),
        ("decimation", DecimationT),
        ("agc", AgcT),
        ("adsbMode", CInt),
    ]


# =============================================================================
# STRUCTURES - Device-Specific (RSP1A/RSP1B)
# =============================================================================


class Rsp1aParamsT(ctypes.Structure):
    """RSP1A device parameters."""

    _fields_ = [
        ("rfNotchEnable", CUChar),
        ("rfDabNotchEnable", CUChar),
    ]


class Rsp1aTunerParamsT(ctypes.Structure):
    """RSP1A tuner parameters."""

    _fields_ = [
        ("biasTEnable", CUChar),
    ]


# =============================================================================
# STRUCTURES - Device-Specific (RSP2)
# =============================================================================


class Rsp2ParamsT(ctypes.Structure):
    """RSP2 device parameters."""

    _fields_ = [
        ("extRefOutputEn", CUChar),
    ]


class Rsp2TunerParamsT(ctypes.Structure):
    """RSP2 tuner parameters."""

    _fields_ = [
        ("biasTEnable", CUChar),
        ("amPortSel", CInt),
        ("antennaSel", CInt),
        ("rfNotchEnable", CUChar),
    ]


# =============================================================================
# STRUCTURES - Device-Specific (RSPduo)
# =============================================================================


class RspDuoParamsT(ctypes.Structure):
    """RSPduo device parameters."""

    _fields_ = [
        ("extRefOutputEn", CInt),
    ]


class RspDuoResetSlaveFlagsT(ctypes.Structure):
    """RSPduo reset slave flags."""

    _fields_ = [
        ("resetGainUpdate", CUChar),
        ("resetRfUpdate", CUChar),
    ]


class RspDuoTunerParamsT(ctypes.Structure):
    """RSPduo tuner parameters."""

    _fields_ = [
        ("biasTEnable", CUChar),
        ("tuner1AmPortSel", CInt),
        ("tuner1AmNotchEnable", CUChar),
        ("rfNotchEnable", CUChar),
        ("rfDabNotchEnable", CUChar),
        ("resetSlaveFlags", RspDuoResetSlaveFlagsT),
    ]


# =============================================================================
# STRUCTURES - Device-Specific (RSPdx/RSPdxR2)
# =============================================================================


class RspDxParamsT(ctypes.Structure):
    """RSPdx device parameters."""

    _fields_ = [
        ("hdrEnable", CUChar),
        ("biasTEnable", CUChar),
        ("antennaSel", CInt),
        ("rfNotchEnable", CUChar),
        ("rfDabNotchEnable", CUChar),
    ]


class RspDxTunerParamsT(ctypes.Structure):
    """RSPdx tuner parameters."""

    _fields_ = [
        ("hdrBw", CInt),
    ]


# =============================================================================
# STRUCTURES - RX Channel Parameters
# =============================================================================


class RxChannelParamsT(ctypes.Structure):
    """Receive channel parameters."""

    _fields_ = [
        ("tunerParams", TunerParamsT),
        ("ctrlParams", ControlParamsT),
        ("rsp1aTunerParams", Rsp1aTunerParamsT),
        ("rsp2TunerParams", Rsp2TunerParamsT),
        ("rspDuoTunerParams", RspDuoTunerParamsT),
        ("rspDxTunerParams", RspDxTunerParamsT),
    ]


# =============================================================================
# STRUCTURES - Device Parameters
# =============================================================================


class FsFreqT(ctypes.Structure):
    """ADC sampling frequency parameters."""

    _fields_ = [
        ("fsHz", CDouble),
        ("syncUpdate", CUChar),
        ("reCal", CUChar),
    ]


class SyncUpdateT(ctypes.Structure):
    """Synchronous update parameters."""

    _fields_ = [
        ("sampleNum", CUInt),
        ("period", CUInt),
    ]


class ResetFlagsT(ctypes.Structure):
    """Reset update operations."""

    _fields_ = [
        ("resetGainUpdate", CUChar),
        ("resetRfUpdate", CUChar),
        ("resetFsUpdate", CUChar),
    ]


class DevParamsT(ctypes.Structure):
    """Non-receive channel device parameters."""

    _fields_ = [
        ("ppm", CDouble),
        ("fsFreq", FsFreqT),
        ("syncUpdate", SyncUpdateT),
        ("resetFlags", ResetFlagsT),
        ("mode", CInt),
        ("samplesPerPkt", CUInt),
        ("rsp1aParams", Rsp1aParamsT),
        ("rsp2Params", Rsp2ParamsT),
        ("rspDuoParams", RspDuoParamsT),
        ("rspDxParams", RspDxParamsT),
    ]


class DeviceParamsT(ctypes.Structure):
    """Top-level device parameters structure."""

    _fields_ = [
        ("devParams", ctypes.POINTER(DevParamsT)),
        ("rxChannelA", ctypes.POINTER(RxChannelParamsT)),
        ("rxChannelB", ctypes.POINTER(RxChannelParamsT)),
    ]


# =============================================================================
# STRUCTURES - Callback Parameters
# =============================================================================


class StreamCbParamsT(ctypes.Structure):
    """Streaming data callback parameters."""

    _fields_ = [
        ("firstSampleNum", CUInt),
        ("grChanged", CInt),
        ("rfChanged", CInt),
        ("fsChanged", CInt),
        ("numSamples", CUInt),
    ]


class GainCbParamT(ctypes.Structure):
    """Gain change event parameters."""

    _fields_ = [
        ("gRdB", CUInt),
        ("lnaGRdB", CUInt),
        ("currGain", CDouble),
    ]


class PowerOverloadCbParamT(ctypes.Structure):
    """Power overload event parameters."""

    _fields_ = [
        ("powerOverloadChangeType", CInt),
    ]


class RspDuoModeCbParamT(ctypes.Structure):
    """RSPduo mode change event parameters."""

    _fields_ = [
        ("modeChangeType", CInt),
    ]


class EventParamsT(ctypes.Union):
    """Event callback parameters union."""

    _fields_ = [
        ("gainParams", GainCbParamT),
        ("powerOverloadParams", PowerOverloadCbParamT),
        ("rspDuoModeParams", RspDuoModeCbParamT),
    ]


# =============================================================================
# CALLBACK FUNCTION TYPES
# =============================================================================

StreamCallbackT = ctypes.CFUNCTYPE(
    None,  # returns void
    CShortPointer,  # xi
    CShortPointer,  # xq
    ctypes.POINTER(StreamCbParamsT),  # params
    CUInt,  # numSamples
    CUInt,  # reset
    CVoidPointer,  # cbContext
)

EventCallbackT = ctypes.CFUNCTYPE(
    None,  # returns void
    CInt,  # eventId
    CInt,  # tuner
    ctypes.POINTER(EventParamsT),  # params
    CVoidPointer,  # cbContext
)


class CallbackFnsT(ctypes.Structure):
    """Callback function pointers structure."""

    _fields_ = [
        ("StreamACbFn", StreamCallbackT),
        ("StreamBCbFn", StreamCallbackT),
        ("EventCbFn", EventCallbackT),
    ]


# Legacy alias for compatibility
CDeviceInfo = DeviceT


# =============================================================================
# FINDING THE LIBRARY
# =============================================================================


def find_sdrplay_library():
    """
    Find the SDRplay API library file on this computer.

    Returns:
        str: Path to the library file.

    Raises:
        SDRPlayError: If the library cannot be found.

    The SDRplay API must be installed from: https://www.sdrplay.com/api/
    """

    # On Windows, check the standard installation folders
    if platform.system() == "Windows":
        possible_paths = [
            r"C:\Program Files\SDRplay\API\x64\sdrplay_api.dll",
            r"C:\Program Files (x86)\SDRplay\API\x64\sdrplay_api.dll",
        ]

        for path in possible_paths:
            if os.path.isfile(path):
                return path

    # On Mac/Linux, ask the system to find it
    library_path = ctypes.util.find_library("sdrplay_api")
    if library_path:
        return library_path

    # Give up with a helpful error message
    raise SDRPlayError(
        "Cannot find SDRplay API library. "
        "Please install it from https://www.sdrplay.com/api/"
    )


def load_sdrplay_library(path):
    """
    Load the SDRplay library file into memory.

    Args:
        path: File path to the library.

    Returns:
        The loaded library object.

    Raises:
        SDRPlayError: If loading fails.
    """
    try:
        # Windows uses a different calling convention than Mac/Linux
        if platform.system() == "Windows":
            return ctypes.WinDLL(path)
        else:
            return ctypes.CDLL(path)
    except OSError as error:
        raise SDRPlayError(f"Cannot load library at {path}: {error}")


# =============================================================================
# API FUNCTION SETUP
# =============================================================================


def setup_api_functions(library):
    """
    Tell Python how to call each function in the C library.

    C functions need to know what types their arguments and return values are.
    This function sets up that information for each API function we use.

    Args:
        library: The loaded SDRplay library.
    """

    # sdrplay_api_Open() - Connect to the API service
    # Takes: nothing
    # Returns: error code (0 = success)
    library.sdrplay_api_Open.argtypes = []
    library.sdrplay_api_Open.restype = CInt

    # sdrplay_api_Close() - Disconnect from the API service
    # Takes: nothing
    # Returns: error code (0 = success)
    library.sdrplay_api_Close.argtypes = []
    library.sdrplay_api_Close.restype = CInt

    # sdrplay_api_ApiVersion() - Get the API version number
    # Takes: pointer to a float (where to store the version)
    # Returns: error code (0 = success)
    library.sdrplay_api_ApiVersion.argtypes = [ctypes.POINTER(CFloat)]
    library.sdrplay_api_ApiVersion.restype = CInt

    # sdrplay_api_GetDevices() - List connected devices
    # Takes: array to fill, pointer to count, max devices
    # Returns: error code (0 = success)
    library.sdrplay_api_GetDevices.argtypes = [
        ctypes.POINTER(CDeviceInfo),  # Array to store device info
        ctypes.POINTER(CUInt),  # Will be set to number of devices found
        CUInt,  # Maximum devices to return
    ]
    library.sdrplay_api_GetDevices.restype = CInt

    # sdrplay_api_SelectDevice() - Choose a device to use
    # Takes: pointer to device info
    # Returns: error code (0 = success)
    library.sdrplay_api_SelectDevice.argtypes = [ctypes.POINTER(CDeviceInfo)]
    library.sdrplay_api_SelectDevice.restype = CInt

    # sdrplay_api_ReleaseDevice() - Stop using a device
    # Takes: pointer to device info
    # Returns: error code (0 = success)
    library.sdrplay_api_ReleaseDevice.argtypes = [ctypes.POINTER(CDeviceInfo)]
    library.sdrplay_api_ReleaseDevice.restype = CInt

    # sdrplay_api_GetErrorString() - Convert error code to message
    library.sdrplay_api_GetErrorString.argtypes = [CInt]
    library.sdrplay_api_GetErrorString.restype = CCharPointer

    # Optional functions - may not exist in all API versions
    try:
        library.sdrplay_api_GetLastError.argtypes = [ctypes.POINTER(DeviceT)]
        library.sdrplay_api_GetLastError.restype = ctypes.POINTER(ErrorInfoT)
    except AttributeError:
        pass

    try:
        library.sdrplay_api_GetLastErrorByType.argtypes = [
            ctypes.POINTER(DeviceT),
            CInt,
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        library.sdrplay_api_GetLastErrorByType.restype = CInt
    except AttributeError:
        pass

    # sdrplay_api_LockDeviceApi() - Lock API for exclusive access
    library.sdrplay_api_LockDeviceApi.argtypes = []
    library.sdrplay_api_LockDeviceApi.restype = CInt

    # sdrplay_api_UnlockDeviceApi() - Release exclusive access
    library.sdrplay_api_UnlockDeviceApi.argtypes = []
    library.sdrplay_api_UnlockDeviceApi.restype = CInt

    try:
        library.sdrplay_api_DisableHeartbeat.argtypes = []
        library.sdrplay_api_DisableHeartbeat.restype = CInt
    except AttributeError:
        pass

    # sdrplay_api_DebugEnable() - Enable/disable debug logging
    library.sdrplay_api_DebugEnable.argtypes = [CVoidPointer, CInt]
    library.sdrplay_api_DebugEnable.restype = CInt

    # sdrplay_api_GetDeviceParams() - Get device parameters structure
    library.sdrplay_api_GetDeviceParams.argtypes = [
        CVoidPointer,
        ctypes.POINTER(ctypes.POINTER(DeviceParamsT)),
    ]
    library.sdrplay_api_GetDeviceParams.restype = CInt

    # sdrplay_api_Init() - Initialize device and start streaming
    library.sdrplay_api_Init.argtypes = [
        CVoidPointer,
        ctypes.POINTER(CallbackFnsT),
        CVoidPointer,
    ]
    library.sdrplay_api_Init.restype = CInt

    # sdrplay_api_Uninit() - Stop streaming and uninitialize
    library.sdrplay_api_Uninit.argtypes = [CVoidPointer]
    library.sdrplay_api_Uninit.restype = CInt

    # sdrplay_api_Update() - Apply parameter changes
    library.sdrplay_api_Update.argtypes = [
        CVoidPointer,
        CInt,  # tuner
        CInt,  # reasonForUpdate
        CInt,  # reasonForUpdateExt1
    ]
    library.sdrplay_api_Update.restype = CInt

    # RSPduo-specific functions (optional)
    try:
        library.sdrplay_api_SwapRspDuoActiveTuner.argtypes = [
            CVoidPointer,
            ctypes.POINTER(CInt),
            CInt,
        ]
        library.sdrplay_api_SwapRspDuoActiveTuner.restype = CInt
    except AttributeError:
        pass

    try:
        library.sdrplay_api_SwapRspDuoDualTunerModeSampleRate.argtypes = [
            CVoidPointer,
            ctypes.POINTER(CDouble),
            CDouble,
        ]
        library.sdrplay_api_SwapRspDuoDualTunerModeSampleRate.restype = CInt
    except AttributeError:
        pass

    try:
        library.sdrplay_api_SwapRspDuoMode.argtypes = [
            ctypes.POINTER(DeviceT),
            ctypes.POINTER(ctypes.POINTER(DeviceParamsT)),
            CInt,
            CDouble,
            CInt,
            CInt,
            CInt,
            CInt,
        ]
        library.sdrplay_api_SwapRspDuoMode.restype = CInt
    except AttributeError:
        pass


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def check_api_result(library, result_code, operation_name):
    """
    Check if an API call succeeded. Raise an error if it failed.

    Args:
        library: The SDRplay library (to get error messages).
        result_code: The return value from the API call.
        operation_name: What we were trying to do (for error messages).

    Raises:
        SDRPlayError: If result_code indicates failure.
    """
    SUCCESS = 0

    if result_code != SUCCESS:
        # Ask the API for a human-readable error message
        error_message = library.sdrplay_api_GetErrorString(result_code)
        error_text = error_message.decode("utf-8") if error_message else "Unknown error"

        raise SDRPlayError(
            f"{operation_name} failed: {error_text} (code {result_code})"
        )


def _make_safe_stream_callback(user_callback):
    """
    Wrap a user's stream callback to prevent exceptions from crashing the C runtime.

    The SDRplay API calls Python callbacks from C threads. If a Python exception
    propagates back into C code, it can crash the entire process. This wrapper
    catches any exception, logs it, and returns safely.

    Args:
        user_callback: The user's callback function.

    Returns:
        A wrapped callback that catches and logs exceptions.
    """

    def safe_wrapper(xi, xq, params, num_samples, reset, ctx):
        try:
            user_callback(xi, xq, params, num_samples, reset, ctx)
        except Exception:
            # Log to stderr but don't crash
            print("SDRplay callback error:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    return safe_wrapper


def _make_safe_event_callback(user_callback):
    """
    Wrap a user's event callback to prevent exceptions from crashing the C runtime.

    Args:
        user_callback: The user's event callback function.

    Returns:
        A wrapped callback that catches and logs exceptions.
    """

    def safe_wrapper(event_id, tuner, params, ctx):
        try:
            user_callback(event_id, tuner, params, ctx)
        except Exception:
            # Log to stderr but don't crash
            print("SDRplay event callback error:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    return safe_wrapper


# =============================================================================
# MAIN CLASS
# =============================================================================


class SDRPlayRadio:
    """
    Control an SDRplay software-defined radio.

    Basic usage with automatic cleanup:

        with SDRPlayRadio() as radio:
            print(f"API Version: {radio.version}")
            print(f"Connected devices: {radio.devices}")

    Manual usage (you must call close() yourself):

        radio = SDRPlayRadio()
        print(radio.devices)
        radio.close()

    Attributes:
        version: The SDRplay API version number (like 3.09).
        devices: List of serial numbers for connected radios.
    """

    def __init__(self, library_path=None):
        """
        Connect to the SDRplay API.

        Args:
            library_path: Optional custom path to the SDRplay library.
                          Usually not needed - the library is found automatically.

        Raises:
            SDRPlayError: If the library cannot be found or loaded.
        """
        # Step 1: Find the library file
        if library_path is None:
            library_path = find_sdrplay_library()

        # Step 2: Load the library into memory
        self._library = load_sdrplay_library(library_path)

        # Step 3: Set up function signatures
        setup_api_functions(self._library)

        # Step 4: Connect to the API service
        result = self._library.sdrplay_api_Open()
        check_api_result(self._library, result, "Opening API connection")

        # Track state
        self._is_open = True
        self._selected_device = None
        self._device_handle = None
        self._device_info = None  # Store copy of DeviceT for release
        self._hw_version = None
        self._selected_tuner = TunerSelect.A  # Default tuner
        self._device_params = None
        self._stream_callback_a = None
        self._stream_callback_b = None
        self._event_callback = None
        self._is_streaming = False
        self._is_dual_tuner = False

    @property
    def version(self):
        """
        Get the SDRplay API version number.

        Returns:
            float: Version number like 3.09

        Example:
            >>> radio.version
            3.09
        """
        # Create a variable to hold the result
        version_number = CFloat()

        # Ask the API for the version
        result = self._library.sdrplay_api_ApiVersion(ctypes.byref(version_number))
        check_api_result(self._library, result, "Getting API version")

        return version_number.value

    @property
    def is_streaming(self):
        """
        Check if the device is currently streaming samples.

        Returns:
            bool: True if streaming is active, False otherwise.

        Example:
            if not radio.is_streaming:
                radio.start(on_samples)
        """
        return self._is_streaming

    @property
    def devices(self):
        """
        List all connected SDRplay devices.

        Returns:
            list[str]: Serial numbers of connected devices.
                       Empty list if no devices are plugged in.

        Example:
            >>> radio.devices
            ['1940005AAA', '1940005BBB']
        """
        MAX_DEVICES = 16

        # Create an array to hold device information
        device_array = (CDeviceInfo * MAX_DEVICES)()

        # Create a variable to hold the count
        device_count = CUInt()

        # Ask the API for the device list
        result = self._library.sdrplay_api_GetDevices(
            device_array, ctypes.byref(device_count), MAX_DEVICES
        )
        check_api_result(self._library, result, "Getting device list")

        # Extract serial numbers from the results
        serial_numbers = []
        for i in range(device_count.value):
            serial_bytes = device_array[i].SerNo
            serial_string = serial_bytes.decode("utf-8").rstrip("\x00")
            serial_numbers.append(serial_string)

        return serial_numbers

    @staticmethod
    def get_device_type_name(hw_version):
        """
        Convert a hardware version ID to a human-readable device name.

        Args:
            hw_version: The hwVer value from the device enumeration.

        Returns:
            str: Device name like "RSP1A", "RSPduo", "RSPdx", etc.
                 Returns "Unknown" if the ID is not recognized.

        Example:
            >>> SDRPlayRadio.get_device_type_name(255)
            'RSP1A'
            >>> SDRPlayRadio.get_device_type_name(3)
            'RSPduo'
        """
        return DEVICE_NAMES.get(hw_version, f"Unknown (hwVer={hw_version})")

    def select_device(self, serial_number, tuner=None):
        """
        Choose a device to use for receiving.

        Args:
            serial_number: The serial number from the devices list.
            tuner: For RSPduo, specify TunerSelect.A or TunerSelect.B.
                   Tuner A = Port 1 (Hi-Z capable), Tuner B = Port 2.

        Raises:
            SDRPlayError: If the device cannot be found or selected,
                          or if currently streaming.

        Example:
            devices = radio.devices
            if devices:
                radio.select_device(devices[0])
            # For RSPduo with antenna on Tuner 2:
            radio.select_device(devices[0], tuner=TunerSelect.B)
        """
        if self._is_streaming:
            raise SDRPlayError(
                "Cannot select a new device while streaming. "
                "Call stop() first to end the current stream."
            )

        MAX_DEVICES = 16

        # Get fresh device list
        device_array = (CDeviceInfo * MAX_DEVICES)()
        device_count = CUInt()

        result = self._library.sdrplay_api_GetDevices(
            device_array, ctypes.byref(device_count), MAX_DEVICES
        )
        check_api_result(self._library, result, "Getting device list")

        # Find the device with matching serial number
        for i in range(device_count.value):
            serial_bytes = device_array[i].SerNo
            serial_string = serial_bytes.decode("utf-8").rstrip("\x00")

            if serial_string == serial_number:
                # For RSPduo, set tuner and mode before selection
                if tuner == TunerSelect.BOTH:
                    # Dual-tuner mode
                    device_array[i].tuner = int(TunerSelect.BOTH)
                    device_array[i].rspDuoMode = RspDuoMode.DUAL_TUNER
                elif tuner is not None:
                    # Single-tuner mode
                    device_array[i].tuner = int(tuner)
                    device_array[i].rspDuoMode = RspDuoMode.SINGLE_TUNER

                # Found it - select this device
                result = self._library.sdrplay_api_SelectDevice(
                    ctypes.byref(device_array[i])
                )
                check_api_result(
                    self._library, result, f"Selecting device {serial_number}"
                )

                # Remember which device we selected and store device info
                self._selected_device = serial_number
                self._device_handle = device_array[i].dev
                self._device_info = DeviceT()  # Make a copy
                ctypes.memmove(
                    ctypes.addressof(self._device_info),
                    ctypes.addressof(device_array[i]),
                    ctypes.sizeof(DeviceT),
                )
                self._hw_version = device_array[i].hwVer
                self._selected_tuner = tuner if tuner is not None else TunerSelect.A
                self._is_dual_tuner = tuner == TunerSelect.BOTH
                return

        # Device not found
        raise SDRPlayError(f"Device with serial '{serial_number}' not found")

    def release_device(self):
        """
        Release the currently selected device.

        Call this when you're done with a device but want to keep
        the API connection open (to select a different device, etc).

        Raises:
            SDRPlayError: If no device is selected or release fails.
        """
        if self._device_info is None:
            raise SDRPlayError("No device is currently selected")

        result = self._library.sdrplay_api_ReleaseDevice(
            ctypes.byref(self._device_info)
        )
        check_api_result(
            self._library, result, f"Releasing device {self._selected_device}"
        )

        self._selected_device = None
        self._device_handle = None
        self._device_info = None
        self._hw_version = None
        self._device_params = None

    def close(self):
        """
        Disconnect from the SDRplay API and free resources.

        This is called automatically if you use `with SDRPlayRadio() as radio:`.
        If you create the radio without `with`, you must call close() yourself.
        """
        if not self._is_open:
            return  # Already closed

        # Release any selected device first
        if self._device_info is not None:
            try:
                self.release_device()
            except SDRPlayError:
                pass  # Best effort - continue closing anyway

        # Close the API connection
        result = self._library.sdrplay_api_Close()
        check_api_result(self._library, result, "Closing API connection")

        self._is_open = False

    # -------------------------------------------------------------------------
    # Streaming and Configuration Methods
    # -------------------------------------------------------------------------

    def get_params(self):
        """
        Get the device parameters structure for the selected device.

        Returns:
            DeviceParamsT: Pointer to device parameters structure.

        Raises:
            SDRPlayError: If no device is selected or params cannot be retrieved.
        """
        if self._device_handle is None:
            raise SDRPlayError("No device is selected")

        # Get device parameters
        params_ptr = ctypes.POINTER(DeviceParamsT)()
        result = self._library.sdrplay_api_GetDeviceParams(
            self._device_handle, ctypes.byref(params_ptr)
        )
        check_api_result(self._library, result, "Getting device parameters")

        self._device_params = params_ptr
        return params_ptr

    @property
    def _rx_channel(self):
        """Get the correct RX channel based on selected tuner."""
        if self._device_params is None:
            self.get_params()
        params = self._device_params.contents
        # For RSPduo Tuner B, use rxChannelB; otherwise rxChannelA
        if self._selected_tuner == TunerSelect.B:
            return params.rxChannelB.contents
        return params.rxChannelA.contents

    @property
    def _rx_channel_a(self):
        """Get Tuner A RX channel params (for dual-tuner mode)."""
        if self._device_params is None:
            self.get_params()
        return self._device_params.contents.rxChannelA.contents

    @property
    def _rx_channel_b(self):
        """Get Tuner B RX channel params (for dual-tuner mode)."""
        if self._device_params is None:
            self.get_params()
        return self._device_params.contents.rxChannelB.contents

    def start(self, on_samples_a, on_samples_b=None, on_event=None):
        """
        Start streaming IQ samples from the device.

        IMPORTANT: Callbacks are invoked from API threads, not the main thread.
        See module docstring for thread safety guidelines.

        Args:
            on_samples_a: Callback for Tuner A (or single-tuner mode)
                          Function signature: (xi, xq, params, num_samples, reset, ctx)
            on_samples_b: Callback for Tuner B (dual-tuner mode only, optional)
                          If None in dual-tuner mode, uses on_samples_a for both
            on_event: Optional event callback(event_id, tuner, params, ctx)

        Raises:
            SDRPlayError: If no device selected or streaming fails to start.
        """
        if self._device_handle is None:
            raise SDRPlayError("No device is selected")

        if self._is_streaming:
            raise SDRPlayError("Already streaming")

        # Wrap user callbacks with exception safety wrappers, then in ctypes
        # function types to prevent garbage collection
        safe_callback_a = _make_safe_stream_callback(on_samples_a)
        self._stream_callback_a = StreamCallbackT(safe_callback_a)

        if on_samples_b is not None:
            safe_callback_b = _make_safe_stream_callback(on_samples_b)
            self._stream_callback_b = StreamCallbackT(safe_callback_b)
        else:
            self._stream_callback_b = self._stream_callback_a

        if on_event:
            safe_event_callback = _make_safe_event_callback(on_event)
            self._event_callback = EventCallbackT(safe_event_callback)
        else:
            # Default event callback that does nothing
            def _default_event(event_id, tuner, params, ctx):
                pass

            self._event_callback = EventCallbackT(_default_event)

        # Create callback structure
        callback_fns = CallbackFnsT()
        callback_fns.StreamACbFn = self._stream_callback_a
        callback_fns.StreamBCbFn = self._stream_callback_b
        callback_fns.EventCbFn = self._event_callback

        # Initialize streaming
        result = self._library.sdrplay_api_Init(
            self._device_handle, ctypes.byref(callback_fns), None
        )

        # If Init fails, clear callback references to allow garbage collection
        if result != 0:
            self._stream_callback_a = None
            self._stream_callback_b = None
            self._event_callback = None
            check_api_result(self._library, result, "Starting stream")

        self._is_streaming = True

    def stop(self):
        """
        Stop streaming IQ samples.

        Raises:
            SDRPlayError: If not streaming or stop fails.
        """
        if not self._is_streaming:
            return  # Not streaming, nothing to do

        if self._device_handle is None:
            raise SDRPlayError("No device is selected")

        result = self._library.sdrplay_api_Uninit(self._device_handle)
        check_api_result(self._library, result, "Stopping stream")

        self._is_streaming = False
        self._stream_callback_a = None
        self._stream_callback_b = None
        self._event_callback = None

    def update(self, reason_flags, ext1_flags=0, tuner=None):
        """
        Apply parameter changes to the device.

        Args:
            reason_flags: Bitmask of ReasonForUpdate values indicating what changed.
            ext1_flags: Bitmask of ReasonForUpdateExt1 values (for RSPdx HDR, etc.)
            tuner: Which tuner to update (None = use selected tuner, or BOTH for dual-tuner)

        Raises:
            SDRPlayError: If update fails.
        """
        if self._device_handle is None:
            raise SDRPlayError("No device is selected")

        target_tuner = tuner if tuner is not None else self._selected_tuner

        result = self._library.sdrplay_api_Update(
            self._device_handle, target_tuner, reason_flags, ext1_flags
        )
        check_api_result(self._library, result, "Updating device parameters")

    def set_frequency(self, hz):
        """
        Set the center frequency.

        Args:
            hz: Frequency in Hz (e.g., 100_000_000 for 100 MHz)
        """
        self._rx_channel.tunerParams.rfFreq.rfHz = float(hz)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_FRF)

    def set_sample_rate(self, hz):
        """
        Set the sample rate.

        Args:
            hz: Sample rate in Hz (e.g., 2_000_000 for 2 MSPS)
        """
        if self._device_params is None:
            self.get_params()

        params = self._device_params.contents
        params.devParams.contents.fsFreq.fsHz = float(hz)

        if self._is_streaming:
            self.update(ReasonForUpdate.DEV_FS)

    def set_bandwidth(self, bw):
        """
        Set the IF bandwidth.

        Args:
            bw: Bandwidth enum value (e.g., Bw.BW_1_536)
        """
        self._rx_channel.tunerParams.bwType = int(bw)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_BW_TYPE)

    def set_gain(self, lna_state, gr_db):
        """
        Set the gain manually.

        Args:
            lna_state: LNA state (0-9 depending on device and frequency)
            gr_db: IF gain reduction in dB (20-59)
        """
        tuner = self._rx_channel.tunerParams
        tuner.gain.gRdB = gr_db
        tuner.gain.LNAstate = lna_state

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_GR)

    # --- Dual-tuner specific methods (RSPduo) ---

    def set_frequency_a(self, hz):
        """
        Set Tuner A center frequency (dual-tuner mode).

        Args:
            hz: Frequency in Hz
        """
        self._rx_channel_a.tunerParams.rfFreq.rfHz = float(hz)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_FRF, tuner=TunerSelect.A)

    def set_frequency_b(self, hz):
        """
        Set Tuner B center frequency (dual-tuner mode).

        Args:
            hz: Frequency in Hz
        """
        self._rx_channel_b.tunerParams.rfFreq.rfHz = float(hz)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_FRF, tuner=TunerSelect.B)

    def set_gain_a(self, lna_state, gr_db):
        """
        Set Tuner A gain (dual-tuner mode).

        Args:
            lna_state: LNA state (0-9 depending on frequency)
            gr_db: IF gain reduction in dB (20-59)
        """
        tuner = self._rx_channel_a.tunerParams
        tuner.gain.gRdB = gr_db
        tuner.gain.LNAstate = lna_state

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_GR, tuner=TunerSelect.A)

    def set_gain_b(self, lna_state, gr_db):
        """
        Set Tuner B gain (dual-tuner mode).

        Args:
            lna_state: LNA state (0-9 depending on frequency)
            gr_db: IF gain reduction in dB (20-59)
        """
        tuner = self._rx_channel_b.tunerParams
        tuner.gain.gRdB = gr_db
        tuner.gain.LNAstate = lna_state

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_GR, tuner=TunerSelect.B)

    # --- RSPduo mode switching methods ---

    def swap_active_tuner(self, tuner1_am_port=None):
        """
        Swap the active tuner on RSPduo in single-tuner mode.

        This allows switching between Tuner A and Tuner B while streaming,
        without stopping and restarting. Only valid for RSPduo in single-tuner mode.

        Args:
            tuner1_am_port: Optional AM port selection for Tuner A after swap.
                           Use RspDuoAmPortSelect.AMPORT_1 or AMPORT_2.
                           Only relevant when switching TO Tuner A.

        Returns:
            TunerSelect: The new active tuner (A or B).

        Raises:
            SDRPlayError: If not RSPduo, not in single-tuner mode, or swap fails.

        Example:
            # Switch from Tuner A to Tuner B
            new_tuner = radio.swap_active_tuner()
            print(f"Now using Tuner {new_tuner.name}")
        """
        if self._hw_version != SDRPLAY_RSPduo_ID:
            raise SDRPlayError("swap_active_tuner() is only valid for RSPduo")

        if self._is_dual_tuner:
            raise SDRPlayError(
                "swap_active_tuner() is only valid in single-tuner mode. "
                "In dual-tuner mode, both tuners are active."
            )

        if not hasattr(self._library, "sdrplay_api_SwapRspDuoActiveTuner"):
            raise SDRPlayError(
                "SwapRspDuoActiveTuner not available in this API version"
            )

        # Prepare tuner output variable
        current_tuner = CInt(int(self._selected_tuner))
        am_port = int(tuner1_am_port) if tuner1_am_port is not None else 0

        result = self._library.sdrplay_api_SwapRspDuoActiveTuner(
            self._device_handle, ctypes.byref(current_tuner), am_port
        )
        check_api_result(self._library, result, "Swapping RSPduo active tuner")

        # Update internal state
        new_tuner = TunerSelect(current_tuner.value)
        self._selected_tuner = new_tuner
        return new_tuner

    def swap_dual_tuner_sample_rate(self, new_sample_rate_hz):
        """
        Change the sample rate for RSPduo in dual-tuner or master/slave mode.

        In dual-tuner mode, the sample rate is shared between both tuners and
        can only be 6 MHz or 8 MHz. This function allows switching between them
        while streaming.

        Args:
            new_sample_rate_hz: New sample rate in Hz.
                               Must be 6_000_000 or 8_000_000.

        Returns:
            float: The previous sample rate in Hz.

        Raises:
            SDRPlayError: If not RSPduo, not in dual-tuner mode, or invalid rate.

        Example:
            old_rate = radio.swap_dual_tuner_sample_rate(8_000_000)
            print(f"Changed from {old_rate/1e6} MHz to 8 MHz")
        """
        if self._hw_version != SDRPLAY_RSPduo_ID:
            raise SDRPlayError("swap_dual_tuner_sample_rate() is only valid for RSPduo")

        if new_sample_rate_hz not in (6_000_000, 8_000_000):
            raise SDRPlayError(
                f"RSPduo dual-tuner mode only supports 6 MHz or 8 MHz sample rate, "
                f"got {new_sample_rate_hz / 1e6} MHz"
            )

        if not hasattr(self._library, "sdrplay_api_SwapRspDuoDualTunerModeSampleRate"):
            raise SDRPlayError(
                "SwapRspDuoDualTunerModeSampleRate not available in this API version"
            )

        # Prepare output variable for current rate
        current_rate = CDouble()

        result = self._library.sdrplay_api_SwapRspDuoDualTunerModeSampleRate(
            self._device_handle, ctypes.byref(current_rate), float(new_sample_rate_hz)
        )
        check_api_result(
            self._library, result, "Swapping RSPduo dual-tuner sample rate"
        )

        return current_rate.value

    def swap_rspduo_mode(self, mode, sample_rate_hz, tuner, bw, if_type, am_port=None):
        """
        Change the RSPduo operating mode while running.

        This powerful function allows switching between single-tuner, dual-tuner,
        master, and slave modes without releasing the device. The device parameters
        pointer may change after this call.

        Args:
            mode: New operating mode (RspDuoMode.SINGLE_TUNER, DUAL_TUNER, etc.)
            sample_rate_hz: Sample rate in Hz for the new mode.
            tuner: Which tuner to use (TunerSelect.A, B, or BOTH for dual-tuner).
            bw: Bandwidth setting (Bw enum value).
            if_type: IF mode setting (If enum value).
            am_port: Optional AM port for Tuner A (RspDuoAmPortSelect).

        Raises:
            SDRPlayError: If not RSPduo or mode change fails.

        Note:
            After calling this, you should call get_params() to refresh the
            device parameters pointer, as it may have changed.

        Example:
            # Switch from single-tuner to dual-tuner mode
            radio.swap_rspduo_mode(
                mode=RspDuoMode.DUAL_TUNER,
                sample_rate_hz=6_000_000,
                tuner=TunerSelect.BOTH,
                bw=Bw.BW_1_536,
                if_type=If.ZERO
            )
            # Refresh params after mode change
            radio.get_params()
        """
        if self._hw_version != SDRPLAY_RSPduo_ID:
            raise SDRPlayError("swap_rspduo_mode() is only valid for RSPduo")

        if self._device_info is None:
            raise SDRPlayError("No device is selected")

        if not hasattr(self._library, "sdrplay_api_SwapRspDuoMode"):
            raise SDRPlayError("SwapRspDuoMode not available in this API version")

        # Prepare output pointer for new params
        new_params_ptr = ctypes.POINTER(DeviceParamsT)()
        am_port_val = int(am_port) if am_port is not None else 0

        result = self._library.sdrplay_api_SwapRspDuoMode(
            ctypes.byref(self._device_info),
            ctypes.byref(new_params_ptr),
            int(mode),
            float(sample_rate_hz),
            int(tuner),
            int(bw),
            int(if_type),
            am_port_val,
        )
        check_api_result(self._library, result, "Swapping RSPduo mode")

        # Update internal state with new params
        self._device_params = new_params_ptr
        self._selected_tuner = tuner
        self._is_dual_tuner = tuner == TunerSelect.BOTH

    def set_agc(self, enabled, setpoint_dbfs=-60):
        """
        Configure automatic gain control.

        Args:
            enabled: True to enable AGC, False for manual gain
            setpoint_dbfs: AGC setpoint in dBFS (default -60)
        """
        ctrl = self._rx_channel.ctrlParams

        if enabled:
            ctrl.agc.enable = AgcControl.AGC_50HZ  # 50Hz AGC loop for FM
            ctrl.agc.setPoint_dBfs = setpoint_dbfs
        else:
            ctrl.agc.enable = AgcControl.DISABLE

        if self._is_streaming:
            self.update(ReasonForUpdate.CTRL_AGC)

    def set_bias_t(self, enabled):
        """
        Enable or disable Bias-T power on the antenna connector.

        Supported on: RSP1A, RSP1B, RSP2, RSPduo, RSPdx, RSPdxR2

        Args:
            enabled: True to enable Bias-T, False to disable
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._device_params is None:
            self.get_params()

        params = self._device_params.contents
        value = 1 if enabled else 0
        reason = None

        if self._hw_version == SDRPLAY_RSP1A_ID:
            # RSP1A Bias-T is in tuner params, not device params
            self._rx_channel.rsp1aTunerParams.biasTEnable = value
            reason = ReasonForUpdate.RSP1A_BIAS_T_CONTROL
        elif self._hw_version == SDRPLAY_RSP1B_ID:
            # RSP1B uses RSP1A parameters
            self._rx_channel.rsp1aTunerParams.biasTEnable = value
            reason = ReasonForUpdate.RSP1A_BIAS_T_CONTROL
        elif self._hw_version == SDRPLAY_RSP2_ID:
            self._rx_channel.rsp2TunerParams.biasTEnable = value
            reason = ReasonForUpdate.RSP2_BIAS_T_CONTROL
        elif self._hw_version == SDRPLAY_RSPduo_ID:
            self._rx_channel.rspDuoTunerParams.biasTEnable = value
            reason = ReasonForUpdate.RSPDUO_BIAS_T_CONTROL
        elif self._hw_version in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            params.devParams.contents.rspDxParams.biasTEnable = value
            reason = ReasonForUpdate.RSPDX_BIAS_T_CONTROL
        else:
            raise SDRPlayError(
                f"Bias-T not supported on this device (hwVer={self._hw_version})"
            )

        if self._is_streaming and reason:
            self.update(reason)

    def set_notch_filter(self, enabled):
        """
        Enable or disable broadcast notch filter.

        Supported on: RSP1A, RSP1B, RSP2, RSPduo, RSPdx, RSPdxR2

        Args:
            enabled: True to enable notch filter, False to disable
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._device_params is None:
            self.get_params()

        params = self._device_params.contents
        value = 1 if enabled else 0
        reason = None

        if self._hw_version == SDRPLAY_RSP1A_ID:
            params.devParams.contents.rsp1aParams.rfNotchEnable = value
            reason = ReasonForUpdate.RSP1A_RF_NOTCH_CONTROL
        elif self._hw_version == SDRPLAY_RSP1B_ID:
            params.devParams.contents.rsp1aParams.rfNotchEnable = value
            reason = ReasonForUpdate.RSP1A_RF_NOTCH_CONTROL
        elif self._hw_version == SDRPLAY_RSP2_ID:
            self._rx_channel.rsp2TunerParams.rfNotchEnable = value
            reason = ReasonForUpdate.RSP2_RF_NOTCH_CONTROL
        elif self._hw_version == SDRPLAY_RSPduo_ID:
            self._rx_channel.rspDuoTunerParams.rfNotchEnable = value
            reason = ReasonForUpdate.RSPDUO_RF_NOTCH_CONTROL
        elif self._hw_version in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            params.devParams.contents.rspDxParams.rfNotchEnable = value
            reason = ReasonForUpdate.RSPDX_RF_NOTCH_CONTROL
        else:
            raise SDRPlayError(
                f"Notch filter not supported on this device (hwVer={self._hw_version})"
            )

        if self._is_streaming and reason:
            self.update(reason)

    def set_antenna(self, antenna):
        """
        Select the antenna input.

        For RSP2: Use Rsp2AntennaSelect.Antenna_A or Antenna_B
        For RSPdx/RSPdxR2: Use RspDxAntennaSelect.Antenna_A, Antenna_B, or Antenna_C

        Args:
            antenna: Antenna selection enum value
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._device_params is None:
            self.get_params()

        params = self._device_params.contents
        reason = None

        if self._hw_version == SDRPLAY_RSP2_ID:
            self._rx_channel.rsp2TunerParams.antennaSel = int(antenna)
            reason = ReasonForUpdate.RSP2_ANTENNA_CONTROL
        elif self._hw_version in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            params.devParams.contents.rspDxParams.antennaSel = int(antenna)
            reason = ReasonForUpdate.RSPDX_ANTENNA_CONTROL
        else:
            raise SDRPlayError(
                f"Antenna selection not supported on this device (hwVer={self._hw_version})"
            )

        if self._is_streaming and reason:
            self.update(reason)

    # -------------------------------------------------------------------------
    # Additional Parameter Methods
    # -------------------------------------------------------------------------

    def set_ppm(self, correction):
        """
        Set frequency correction in parts per million (PPM).

        Args:
            correction: PPM correction value (e.g., 0.5 for +0.5 ppm)
        """
        if self._device_params is None:
            self.get_params()

        self._device_params.contents.devParams.contents.ppm = float(correction)

        if self._is_streaming:
            self.update(ReasonForUpdate.DEV_PPM)

    def set_if_mode(self, mode):
        """
        Set the IF mode.

        Args:
            mode: IF mode (If.ZERO, If.IF_0_450, If.IF_1_620, If.IF_2_048)
        """
        self._rx_channel.tunerParams.ifType = int(mode)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_IF_TYPE)

    def set_lo_mode(self, mode):
        """
        Set the LO mode for frequencies 24-500 MHz.

        Args:
            mode: LO mode (LoMode.AUTO, LoMode.LO_120MHZ, LoMode.LO_144MHZ, LoMode.LO_168MHZ)
        """
        self._rx_channel.tunerParams.loMode = int(mode)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_LO_MODE)

    def set_decimation(self, enable, factor=1):
        """
        Enable hardware decimation to reduce effective sample rate.

        Args:
            enable: True to enable decimation
            factor: Decimation factor (1, 2, 4, 8, 16, or 32)
        """
        ctrl = self._rx_channel.ctrlParams
        ctrl.decimation.enable = 1 if enable else 0
        ctrl.decimation.decimationFactor = factor

        if self._is_streaming:
            self.update(ReasonForUpdate.CTRL_DECIMATION)

    def set_dc_offset(self, dc_enable=True, iq_enable=True):
        """
        Enable DC offset and IQ imbalance correction.

        Args:
            dc_enable: Enable DC offset correction (default True)
            iq_enable: Enable IQ imbalance correction (default True)
        """
        ctrl = self._rx_channel.ctrlParams
        ctrl.dcOffset.DCenable = 1 if dc_enable else 0
        ctrl.dcOffset.IQenable = 1 if iq_enable else 0

        if self._is_streaming:
            self.update(ReasonForUpdate.CTRL_DC_OFFSET_IQ_IMBALANCE)

    def set_adsb_mode(self, mode):
        """
        Set ADS-B optimized mode for 1090 MHz reception.

        Args:
            mode: ADS-B mode (AdsbMode.DECIMATION, NO_DECIMATION_LOWPASS, etc.)
        """
        self._rx_channel.ctrlParams.adsbMode = int(mode)

        if self._is_streaming:
            self.update(ReasonForUpdate.CTRL_ADSB_MODE)

    def set_dab_notch(self, enabled):
        """
        Enable DAB band notch filter.

        Supported on: RSP1A, RSP1B, RSPduo, RSPdx, RSPdxR2

        Args:
            enabled: True to enable DAB notch filter
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._device_params is None:
            self.get_params()

        value = 1 if enabled else 0
        reason = None

        if self._hw_version in (SDRPLAY_RSP1A_ID, SDRPLAY_RSP1B_ID):
            self._device_params.contents.devParams.contents.rsp1aParams.rfDabNotchEnable = value
            reason = ReasonForUpdate.RSP1A_RF_DAB_NOTCH_CONTROL
        elif self._hw_version == SDRPLAY_RSPduo_ID:
            self._rx_channel.rspDuoTunerParams.rfDabNotchEnable = value
            reason = ReasonForUpdate.RSPDUO_RF_DAB_NOTCH_CONTROL
        elif self._hw_version in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            self._device_params.contents.devParams.contents.rspDxParams.rfDabNotchEnable = value
            reason = ReasonForUpdate.RSPDX_RF_DAB_NOTCH_CONTROL
        else:
            raise SDRPlayError(
                f"DAB notch not supported on this device (hwVer={self._hw_version})"
            )

        if self._is_streaming and reason:
            self.update(reason)

    def set_am_port(self, port):
        """
        Select AM port input.

        For RSP2: Use Rsp2AmPortSelect.AMPORT_1 or AMPORT_2
        For RSPduo: Use RspDuoAmPortSelect.AMPORT_1 or AMPORT_2

        Args:
            port: AM port selection enum value
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._hw_version == SDRPLAY_RSP2_ID:
            self._rx_channel.rsp2TunerParams.amPortSel = int(port)
            if self._is_streaming:
                self.update(ReasonForUpdate.RSP2_AM_PORT_SELECT)
        elif self._hw_version == SDRPLAY_RSPduo_ID:
            self._rx_channel.rspDuoTunerParams.tuner1AmPortSel = int(port)
            if self._is_streaming:
                self.update(ReasonForUpdate.RSPDUO_AM_PORT_SELECT)
        else:
            raise SDRPlayError(
                f"AM port not supported on this device (hwVer={self._hw_version})"
            )

    def set_hdr_mode(self, enabled, bandwidth=None):
        """
        Enable HDR (High Dynamic Range) mode for frequencies below 2 MHz.

        RSPdx/RSPdxR2 only.

        Args:
            enabled: True to enable HDR mode
            bandwidth: Optional HDR bandwidth (RspDxHdrModeBw.BW_0_200, etc.)
        """
        if self._hw_version not in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            raise SDRPlayError("HDR mode only supported on RSPdx/RSPdxR2")

        if self._device_params is None:
            self.get_params()

        self._device_params.contents.devParams.contents.rspDxParams.hdrEnable = (
            1 if enabled else 0
        )

        if self._is_streaming:
            self.update(0, ReasonForUpdateExt1.RSPDX_HDR_ENABLE)

        if bandwidth is not None:
            self._rx_channel.rspDxTunerParams.hdrBw = int(bandwidth)
            if self._is_streaming:
                self.update(0, ReasonForUpdateExt1.RSPDX_HDR_BW)

    def set_external_ref(self, enabled):
        """
        Enable external reference clock output.

        Supported on: RSP2, RSPduo

        Args:
            enabled: True to enable external reference output
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        if self._device_params is None:
            self.get_params()

        value = 1 if enabled else 0

        if self._hw_version == SDRPLAY_RSP2_ID:
            self._device_params.contents.devParams.contents.rsp2Params.extRefOutputEn = value
            if self._is_streaming:
                self.update(ReasonForUpdate.RSP2_EXT_REF_CONTROL)
        elif self._hw_version == SDRPLAY_RSPduo_ID:
            self._device_params.contents.devParams.contents.rspDuoParams.extRefOutputEn = value
            if self._is_streaming:
                self.update(ReasonForUpdate.RSPDUO_EXT_REF_CONTROL)
        else:
            raise SDRPlayError(
                f"External ref not supported on this device (hwVer={self._hw_version})"
            )

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    def get_device_info(self):
        """
        Get detailed information about the currently selected device.

        Returns:
            dict: Device information including:
                - 'serial': Serial number string
                - 'type': Device type name ("RSP1A", "RSPduo", etc.)
                - 'hw_version': Hardware version ID (numeric)
                - 'tuner': Active tuner (TunerSelect enum)
                - 'is_dual_tuner': True if in dual-tuner mode
                - 'is_streaming': True if currently streaming

        Raises:
            SDRPlayError: If no device is selected.

        Example:
            info = radio.get_device_info()
            print(f"Using {info['type']} (serial: {info['serial']})")
        """
        if self._device_info is None:
            raise SDRPlayError("No device is selected")

        return {
            "serial": self._selected_device,
            "type": DEVICE_NAMES.get(self._hw_version, f"Unknown ({self._hw_version})"),
            "hw_version": self._hw_version,
            "tuner": self._selected_tuner,
            "is_dual_tuner": self._is_dual_tuner,
            "is_streaming": self._is_streaming,
        }

    def get_current_gain(self):
        """
        Get the current gain values from the device.

        The gain values are populated by the API after streaming starts
        and are updated whenever gain changes occur.

        Returns:
            dict: Current gain information including:
                - 'current_db': Current overall gain in dB
                - 'max_db': Maximum possible gain in dB
                - 'min_db': Minimum possible gain in dB
                - 'lna_state': Current LNA state (0 to N)
                - 'gr_db': Current IF gain reduction in dB

        Raises:
            SDRPlayError: If no device is selected.

        Example:
            gain = radio.get_current_gain()
            print(f"Current gain: {gain['current_db']:.1f} dB")
            print(f"Range: {gain['min_db']:.1f} to {gain['max_db']:.1f} dB")
        """
        if self._device_params is None:
            self.get_params()

        tuner_params = self._rx_channel.tunerParams
        gain_vals = tuner_params.gain.gainVals

        return {
            "current_db": gain_vals.curr,
            "max_db": gain_vals.max,
            "min_db": gain_vals.min,
            "lna_state": tuner_params.gain.LNAstate,
            "gr_db": tuner_params.gain.gRdB,
        }

    def get_samples_per_packet(self):
        """
        Get the number of samples per callback packet.

        This value is set by the API after device selection and indicates
        how many samples will be delivered in each streaming callback.

        Returns:
            int: Number of samples per packet.

        Raises:
            SDRPlayError: If no device is selected.

        Example:
            samples_per_pkt = radio.get_samples_per_packet()
            print(f"Expecting {samples_per_pkt} samples per callback")
        """
        if self._device_params is None:
            self.get_params()

        return self._device_params.contents.devParams.contents.samplesPerPkt

    def acknowledge_power_overload(self):
        """
        Acknowledge a power overload event from the device.

        When the device detects an ADC overload, it sends a POWER_OVERLOAD_CHANGE
        event to your event callback. You MUST call this method to acknowledge
        the event, otherwise the device may stop sending samples.

        Call this from your event callback when you receive an overload event.

        Raises:
            SDRPlayError: If no device is selected or update fails.

        Example:
            def on_event(event_id, tuner, params, ctx):
                if event_id == Event.POWER_OVERLOAD_CHANGE:
                    overload_type = params.contents.powerOverloadParams.powerOverloadChangeType
                    if overload_type == PowerOverloadCbEventId.OVERLOAD_DETECTED:
                        print("Power overload detected!")
                        radio.acknowledge_power_overload()
        """
        if self._device_handle is None:
            raise SDRPlayError("No device is selected")

        self.update(ReasonForUpdate.CTRL_OVERLOAD_MSG_ACK)

    def disable_heartbeat(self):
        """
        Disable the API heartbeat mechanism for debugging.

        The API normally sends periodic heartbeat messages to verify the
        connection. When stepping through code in a debugger, these
        heartbeats can timeout and cause disconnection.

        IMPORTANT: This function MUST be called BEFORE select_device().

        WARNING: Only use this for debugging. In normal operation, the
        heartbeat helps detect hung applications.

        Raises:
            SDRPlayError: If the API doesn't support this or if a device
                          is already selected.

        Example:
            radio = SDRPlayRadio()
            radio.disable_heartbeat()  # Before selecting device!
            radio.select_device(radio.devices[0])
            # Now you can step through code in debugger
        """
        if self._device_handle is not None:
            raise SDRPlayError(
                "disable_heartbeat() must be called before select_device()"
            )

        if not hasattr(self._library, "sdrplay_api_DisableHeartbeat"):
            raise SDRPlayError("DisableHeartbeat not available in this API version")

        result = self._library.sdrplay_api_DisableHeartbeat()
        check_api_result(self._library, result, "Disabling heartbeat")

    def set_am_notch(self, enabled):
        """
        Enable or disable the AM band notch filter on RSPduo Tuner A.

        This is a SEPARATE filter from the RF notch filter set by set_notch_filter().
        The AM notch filter specifically attenuates AM broadcast interference
        (535 kHz - 1.7 MHz) when receiving on Tuner A.

        RSPduo only, Tuner A only.

        Args:
            enabled: True to enable AM notch, False to disable.

        Raises:
            SDRPlayError: If device is not RSPduo or not using Tuner A.

        Example:
            # Enable AM notch to reduce broadcast interference
            radio.set_am_notch(True)
        """
        if self._hw_version != SDRPLAY_RSPduo_ID:
            raise SDRPlayError("AM notch filter is only available on RSPduo")

        if self._selected_tuner == TunerSelect.B:
            raise SDRPlayError(
                "AM notch filter is only available on RSPduo Tuner A. "
                "Current tuner is Tuner B."
            )

        self._rx_channel.rspDuoTunerParams.tuner1AmNotchEnable = 1 if enabled else 0

        if self._is_streaming:
            self.update(ReasonForUpdate.RSPDUO_TUNER1_AM_NOTCH_CONTROL)

    def set_min_gain_reduction(self, mode):
        """
        Set the minimum gain reduction mode.

        The API supports two modes for gain reduction:
        - NORMAL_MIN_GR (20): Standard mode, minimum GR is 20 dB
        - EXTENDED_MIN_GR (0): Extended mode, minimum GR is 0 dB

        Extended mode allows lower gain reduction but may increase noise floor.

        Args:
            mode: MinGainReduction.NORMAL_MIN_GR or MinGainReduction.EXTENDED_MIN_GR

        Example:
            # Use extended range for weak signals
            radio.set_min_gain_reduction(MinGainReduction.EXTENDED_MIN_GR)
        """
        if self._device_params is None:
            self.get_params()

        self._rx_channel.tunerParams.gain.minGr = int(mode)

        if self._is_streaming:
            self.update(ReasonForUpdate.TUNER_GR_LIMITS)

    def set_transfer_mode(self, mode):
        """
        Set the USB transfer mode.

        Most users should leave this at the default (ISOCH). BULK mode
        may be useful for certain USB host controllers that have issues
        with isochronous transfers.

        IMPORTANT: Must be called BEFORE start().

        Args:
            mode: TransferMode.ISOCH (default) or TransferMode.BULK

        Raises:
            SDRPlayError: If called while streaming.

        Example:
            radio.set_transfer_mode(TransferMode.BULK)
            radio.start(on_samples)
        """
        if self._is_streaming:
            raise SDRPlayError(
                "Cannot change transfer mode while streaming. Call stop() first."
            )

        if self._device_params is None:
            self.get_params()

        self._device_params.contents.devParams.contents.mode = int(mode)

    # -------------------------------------------------------------------------
    # Validation Helpers
    # -------------------------------------------------------------------------

    def get_max_lna_state(self, frequency_hz=None):
        """
        Get the maximum valid LNA state for the current device and frequency.

        LNA states range from 0 (minimum attenuation) to N-1 (maximum attenuation),
        where N depends on the device type and frequency band. This method returns
        the maximum valid state number for the given configuration.

        Args:
            frequency_hz: Frequency in Hz to check. If None, uses the current
                         tuner frequency setting.

        Returns:
            int: Maximum valid LNA state (0 to this value inclusive).

        Raises:
            SDRPlayError: If no device is selected.

        Example:
            max_lna = radio.get_max_lna_state()
            print(f"Valid LNA states: 0 to {max_lna}")
            # Set to maximum attenuation
            radio.set_gain(lna_state=max_lna, gr_db=59)
        """
        if self._hw_version is None:
            raise SDRPlayError("No device is selected")

        # Get frequency if not provided
        if frequency_hz is None:
            if self._device_params is None:
                self.get_params()
            frequency_hz = self._rx_channel.tunerParams.rfFreq.rfHz

        freq_mhz = frequency_hz / 1_000_000

        # RSP1 (original) - always 4 states
        if self._hw_version == SDRPLAY_RSP1_ID:
            return RSP1_NUM_LNA_STATES - 1

        # RSP1A / RSP1B
        if self._hw_version in (SDRPLAY_RSP1A_ID, SDRPLAY_RSP1B_ID):
            if freq_mhz < 60:
                return RSP1A_NUM_LNA_STATES_AM - 1
            elif freq_mhz < 1000:
                return RSP1A_NUM_LNA_STATES - 1
            else:
                return RSP1A_NUM_LNA_STATES_LBAND - 1

        # RSP2
        if self._hw_version == SDRPLAY_RSP2_ID:
            # Check if Hi-Z port selected
            try:
                antenna = self._rx_channel.rsp2TunerParams.antennaSel
                if antenna == 0:  # Hi-Z
                    return RSP2_NUM_LNA_STATES_HIZ - 1
            except (AttributeError, ValueError):
                pass

            if freq_mhz < 60:
                return RSP2_NUM_LNA_STATES_AM - 1
            elif freq_mhz < 420:
                return RSP2_NUM_LNA_STATES_VHF - 1
            elif freq_mhz < 1000:
                return RSP2_NUM_LNA_STATES - 1
            else:
                return RSP2_NUM_LNA_STATES_LBAND - 1

        # RSPduo
        if self._hw_version == SDRPLAY_RSPduo_ID:
            # Check if Hi-Z port selected on Tuner A
            try:
                am_port = self._rx_channel.rspDuoTunerParams.tuner1AmPortSel
                if am_port == RspDuoAmPortSelect.AMPORT_1 and freq_mhz < 60:
                    return RSPDUO_NUM_LNA_STATES_HIZ - 1
            except (AttributeError, ValueError):
                pass

            if freq_mhz < 60:
                return RSPDUO_NUM_LNA_STATES_AM - 1
            elif freq_mhz < 1000:
                return RSPDUO_NUM_LNA_STATES - 1
            else:
                return RSPDUO_NUM_LNA_STATES_LBAND - 1

        # RSPdx / RSPdxR2
        if self._hw_version in (SDRPLAY_RSPdx_ID, SDRPLAY_RSPdxR2_ID):
            # Check if HDR mode is enabled
            try:
                if self._device_params is not None:
                    hdr = self._device_params.contents.devParams.contents.rspDxParams.hdrEnable
                    if hdr and freq_mhz < 2:
                        return RSPDX_NUM_LNA_STATES_HDR - 1
            except (AttributeError, ValueError):
                pass

            if freq_mhz < 12:
                return RSPDX_NUM_LNA_STATES_AM - 1
            elif freq_mhz < 50:
                return RSPDX_NUM_LNA_STATES_VLF - 1
            elif freq_mhz < 200:
                return RSPDX_NUM_LNA_STATES_VHF - 1
            elif freq_mhz < 1000:
                return RSPDX_NUM_LNA_STATES - 1
            else:
                return RSPDX_NUM_LNA_STATES_LBAND - 1

        # Unknown device - return a safe default
        return 3

    def validate_lna_state(self, lna_state, frequency_hz=None):
        """
        Check if an LNA state is valid for the current device and frequency.

        Args:
            lna_state: The LNA state to validate (0 to N).
            frequency_hz: Optional frequency in Hz. If None, uses current frequency.

        Returns:
            bool: True if the LNA state is valid.

        Raises:
            SDRPlayError: If no device is selected.

        Example:
            if radio.validate_lna_state(5):
                radio.set_gain(lna_state=5, gr_db=40)
            else:
                print("LNA state 5 is not valid for this configuration")
        """
        max_state = self.get_max_lna_state(frequency_hz)
        return 0 <= lna_state <= max_state

    def validate_sample_rate(self, sample_rate_hz):
        """
        Check if a sample rate is valid and explain any restrictions.

        The SDRplay API supports sample rates from 2 MHz to 10.66 MHz in normal
        mode, with specific restrictions for certain configurations.

        Args:
            sample_rate_hz: Proposed sample rate in Hz.

        Returns:
            tuple: (is_valid, message) where is_valid is bool and message
                   explains any issues or confirms validity.

        Example:
            valid, msg = radio.validate_sample_rate(8_000_000)
            if not valid:
                print(f"Invalid sample rate: {msg}")
        """
        rate_mhz = sample_rate_hz / 1_000_000

        # RSPduo dual-tuner mode restrictions
        if self._is_dual_tuner:
            if sample_rate_hz in (6_000_000, 8_000_000):
                return True, f"Valid for RSPduo dual-tuner mode: {rate_mhz} MHz"
            else:
                return False, (
                    f"RSPduo dual-tuner mode only supports 6 MHz or 8 MHz. "
                    f"Requested: {rate_mhz} MHz"
                )

        # General sample rate limits
        min_rate = 2_000_000
        max_rate = 10_660_000

        if sample_rate_hz < min_rate:
            return False, f"Sample rate {rate_mhz} MHz is below minimum of 2 MHz"

        if sample_rate_hz > max_rate:
            return False, f"Sample rate {rate_mhz} MHz exceeds maximum of 10.66 MHz"

        # LIF mode requires specific rates
        if self._device_params is not None:
            try:
                if_type = self._rx_channel.tunerParams.ifType
                # Low-IF modes require 6-8 MHz
                if if_type in (If.IF_0_450, If.IF_1_620, If.IF_2_048):
                    if sample_rate_hz not in (6_000_000, 7_000_000, 8_000_000):
                        return False, (
                            f"Low-IF mode (IF={if_type.name}) requires "
                            f"6, 7, or 8 MHz sample rate. Requested: {rate_mhz} MHz"
                        )
            except (AttributeError, ValueError):
                pass

        return True, f"Valid sample rate: {rate_mhz} MHz"

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def get_last_error(self):
        """
        Get extended error information from the last failed API call.

        The SDRplay API maintains extended error information that can help
        diagnose issues beyond the basic error codes. This method retrieves
        that information.

        Returns:
            dict: Extended error information including:
                - 'file': Source file where error occurred (API internal)
                - 'function': Function name where error occurred
                - 'line': Line number in API source
                - 'message': Human-readable error message

            Returns None if GetLastError is not available or no error info.

        Example:
            try:
                radio.set_frequency(999_999_999_999)  # Invalid frequency
            except SDRPlayError as e:
                error_info = radio.get_last_error()
                if error_info:
                    print(f"Error in {error_info['function']}: {error_info['message']}")
        """
        if not hasattr(self._library, "sdrplay_api_GetLastError"):
            return None

        try:
            error_ptr = self._library.sdrplay_api_GetLastError(
                ctypes.byref(self._device_info) if self._device_info else None
            )
            if error_ptr:
                error = error_ptr.contents
                return {
                    "file": error.file.decode("utf-8", errors="replace").rstrip("\x00"),
                    "function": error.function.decode("utf-8", errors="replace").rstrip(
                        "\x00"
                    ),
                    "line": error.line,
                    "message": error.message.decode("utf-8", errors="replace").rstrip(
                        "\x00"
                    ),
                }
        except Exception:
            pass

        return None

    def debug_enable(self, level=DbgLvl.WARNING):
        """
        Enable API debug logging.

        Args:
            level: Debug level (DbgLvl.DISABLE, VERBOSE, WARNING, ERROR, MESSAGE)
        """
        handle = self._device_handle if self._device_handle else None
        self._library.sdrplay_api_DebugEnable(handle, int(level))

    def lock_api(self):
        """
        Lock the API for exclusive device enumeration access.

        Use this when multiple processes may access the API simultaneously.
        Call unlock_api() when done.
        """
        result = self._library.sdrplay_api_LockDeviceApi()
        check_api_result(self._library, result, "Locking API")

    def unlock_api(self):
        """
        Unlock the API after exclusive access.
        """
        result = self._library.sdrplay_api_UnlockDeviceApi()
        check_api_result(self._library, result, "Unlocking API")

    def __enter__(self):
        """
        Called when entering a `with` block.

        Example:
            with SDRPlayRadio() as radio:
                print(radio.version)
        """
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        """
        Called when exiting a `with` block. Automatically cleans up.
        """
        self.close()


# =============================================================================
# QUICK TEST
# =============================================================================

if __name__ == "__main__":
    print("SDRplay Radio Test")
    print("==================")
    print()

    try:
        with SDRPlayRadio() as radio:
            print(f"API Version: {radio.version}")
            print(f"Devices: {radio.devices}")

            if not radio.devices:
                print("No devices connected.")
            else:
                print()
                print(f"Selecting first device: {radio.devices[0]}")
                radio.select_device(radio.devices[0])
                print("Device selected successfully!")

                # Get and display device parameters
                params = radio.get_params()
                dev_params = params.contents.devParams.contents
                print(f"Default sample rate: {dev_params.fsFreq.fsHz} Hz")

                # Configure the device
                radio.set_frequency(100_000_000)  # 100 MHz
                radio.set_sample_rate(2_000_000)  # 2 MSPS
                radio.set_bandwidth(Bw.BW_1_536)
                radio.set_agc(True, setpoint_dbfs=-30)
                print("Configured: 100 MHz, 2 MSPS, 1.536 MHz BW, AGC on")

                # Stream for 2 seconds
                samples_received = [0]  # Use list for closure

                def on_samples(xi, xq, params, num_samples, reset, ctx):
                    samples_received[0] += num_samples

                print()
                print("Streaming for 2 seconds...")
                radio.start(on_samples)
                time.sleep(2)
                radio.stop()

                print(f"Received {samples_received[0]:,} samples")
                print(f"Effective rate: {samples_received[0] / 2:,.0f} samples/sec")

                radio.release_device()
                print("Device released.")

    except SDRPlayError as error:
        print(f"Error: {error}")
