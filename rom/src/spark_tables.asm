; Cleanroom 68HC11 Spark Advance, Knock Retard & Ignition Dwell Module
; Tables match rom/models/spark_twin.py default_spark_calibration() byte-for-byte.

        .org    0x9000

;@table_start BASE_SPK_TBL
BASE_SPK_TBL:
        .byte   0x05
        .byte   0x20, 0x24, 0x00, 0x60
        .byte   0x40, 0x30, 0x00, 0x72
        .byte   0x64, 0x40, 0x00, 0x3D
        .byte   0x96, 0x4C, 0x00, 0x14
        .byte   0xC8, 0x50, 0x00, 0x00
;@table_end BASE_SPK_TBL

;@table_start MAP_RETARD_TBL
MAP_RETARD_TBL:
        .byte   0x04
        .byte   0x7E, 0x00, 0x00, 0x2F
        .byte   0xAA, 0x08, 0x00, 0x46
        .byte   0xD6, 0x14, 0x00, 0x51
        .byte   0xFC, 0x20, 0x00, 0x00
;@table_end MAP_RETARD_TBL

;@table_start DLTKNK_TBL
DLTKNK_TBL:
        .byte   0x04
        .byte   0x20, 0x80, 0x00, 0x40
        .byte   0x60, 0x90, 0x00, 0x40
        .byte   0xA0, 0xA0, 0x00, 0x40
        .byte   0xE0, 0xB0, 0x00, 0x00
;@table_end DLTKNK_TBL

;@table_start RETMAX_TBL
RETMAX_TBL:
        .byte   0x04
        .byte   0x65, 0x04, 0x00, 0x10
        .byte   0xA4, 0x08, 0x00, 0x14
        .byte   0xD6, 0x0C, 0x00, 0x1B
        .byte   0xFC, 0x10, 0x00, 0x00
;@table_end RETMAX_TBL

;@table_start SPKLMT_TBL
SPKLMT_TBL:
        .byte   0x04
        .byte   0x20, 0x50, 0x00, 0x55
        .byte   0x50, 0x60, 0x00, 0x40
        .byte   0x90, 0x70, 0x00, 0x20
        .byte   0xD0, 0x78, 0x00, 0x00
;@table_end SPKLMT_TBL

;@table_start DWELL_TBL
DWELL_TBL:
        .byte   0x7E, 0xEC, 0x77, 0x25, 0x80
        .byte   0x9E, 0xF1, 0x27, 0x19, 0x00
        .byte   0xBE, 0xF4, 0x47, 0x13, 0x3B
        .byte   0xD8, 0xF6, 0x3B, 0x15, 0x0D
        .byte   0xEB, 0xF7, 0xCB, 0x00, 0x00
        .byte   0xFF
;@table_end DWELL_TBL

KNKLIM_MAX_U8:
        .byte   0x14
MAX_SAFE_ADV_U8:
        .byte   0x7F
MIN_ADWELL_U16:
        .word   0x0080

; --- Spark & Dwell Evaluation Code ---
        .org    0x9400

; CALC_SPARK_ADVANCE: Evaluate base spark, MAP retard, knock retard & limits
; Exit: A = RAM_SPARK_ADV (0.5 deg/bit BTDC)
CALC_SPARK_ADVANCE:
        LDAA    *RAM_RPM_HB
        LDX     #BASE_SPK_TBL
        JSR     LOOKUP4
        STAA    *RAM_TMP8_A

        LDAA    *RAM_MAP_RAW
        LDX     #MAP_RETARD_TBL
        JSR     LOOKUP4
        TAB
        LDAA    *RAM_TMP8_A
        SBA
        BCC     CSA_MAP_OK
        CLRA
CSA_MAP_OK:
        STAA    *RAM_TMP8_A

        LDAA    *RAM_RPM_HB
        LDX     #DLTKNK_TBL
        JSR     LOOKUP4
        STAA    *RAM_TMP8_B
        LDAA    *RAM_KNOCK_RAW
        CMPA    *RAM_TMP8_B
        BHI     CSA_KNOCK_ACTIVE
        CLRA
        BRA     CSA_STORE_KNK
CSA_KNOCK_ACTIVE:
        LDAA    *RAM_MAP_RAW
        LDX     #RETMAX_TBL
        JSR     LOOKUP4
        CMPA    KNKLIM_MAX_U8
        BLS     CSA_STORE_KNK
        LDAA    KNKLIM_MAX_U8
CSA_STORE_KNK:
        STAA    *RAM_KNOCK_RETARD

        LDAA    *RAM_TMP8_A
        SUBA    *RAM_KNOCK_RETARD
        BCC     CSA_KNK_OK
        CLRA
CSA_KNK_OK:
        STAA    *RAM_TMP8_A

        LDAA    *RAM_RPM_HB
        LDX     #SPKLMT_TBL
        JSR     LOOKUP4
        CMPA    MAX_SAFE_ADV_U8
        BLS     CSA_LMT_READY
        LDAA    MAX_SAFE_ADV_U8
CSA_LMT_READY:
        STAA    *RAM_TMP8_B
        LDAA    *RAM_TMP8_A
        CMPA    *RAM_TMP8_B
        BLS     CSA_FINAL_OK
        LDAA    *RAM_TMP8_B
CSA_FINAL_OK:
        STAA    *RAM_SPARK_ADV
        RTS

; CALC_DWELL_TICKS: Look up 1's-complemented dwell table and invert into D/RAM
CALC_DWELL_TICKS:
        LDAA    *RAM_BATT_RAW
        LDX     #DWELL_TBL
        JSR     LOOKUP5
        COMA
        COMB
        STD     *RAM_DWELL_HB
        RTS
