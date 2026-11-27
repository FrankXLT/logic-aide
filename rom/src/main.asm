; Cleanroom 68HC11 ECU Kernel - Entry Point, Memory Map & Main Loop
; Target: Motorola MC68HC11 (32 KB ROM mapped at 0x8000-0xFFFF)
; Syntax: 2-pass AS11 / ASxxxx compatible (LF line endings)

; --- Zero-Page RAM Equates ($001F-$0055) ---
RAM_SIM_FLAGS     == 0x001F    ; Bit 0: 1=read ADC registers, 0=use injected RAM
RAM_RPM_HB        == 0x0020    ; Engine speed high byte (32 RPM/bit)
RAM_MAP_RAW       == 0x0021    ; Manifold absolute pressure (uint8)
RAM_BARO_RAW      == 0x0022    ; Barometric pressure (uint8)
RAM_BATT_RAW      == 0x0023    ; Battery voltage raw ADC (uint8)
RAM_CTS_RAW       == 0x0024    ; Coolant temperature raw ADC (uint8)
RAM_ACT_RAW       == 0x0025    ; Air charge temperature raw ADC (uint8)
RAM_TPS_RAW       == 0x0026    ; Throttle position raw ADC (uint8)
RAM_KNOCK_RAW     == 0x0027    ; Knock sensor raw ADC (uint8)
RAM_FUEL_TRIM_U8  == 0x0028    ; Enrichment trim factor (0 = disabled)

RAM_BASE_PW_HB    == 0x0030    ; Base pulse width from WOTTBL (uint16)
RAM_PEF_U8        == 0x0032    ; Volumetric efficiency factor from PEFTBL (uint8)
RAM_BATOFF_U8     == 0x0033    ; Battery deadtime from BATOFF_TBL (uint8)
RAM_FUEL_PW_HB    == 0x0034    ; Final injector pulse width in 4 us ticks (uint16)
RAM_DIST_PW_HB    == 0x0036    ; Distributor event period in timer ticks (uint16)
RAM_KNOCK_RETARD  == 0x0038    ; Active knock retard (0.5 deg/bit)
RAM_SPARK_ADV     == 0x0039    ; Final spark advance BTDC (0.5 deg/bit)
RAM_DWELL_HB      == 0x003A    ; Ignition coil dwell ticks (uint16)
RAM_WG_DUTY       == 0x003C    ; Wastegate solenoid duty (0..200)
RAM_CUTOUT_FLAGS  == 0x003D    ; Bit 0=overboost, Bit 1=rev limit, Bit 2=fuel cut
RAM_SCI_LAST_RX   == 0x003E    ; Last SCI byte received
RAM_SCI_LAST_TX   == 0x003F    ; Last SCI byte transmitted

RAM_TMP16_A       == 0x0050    ; 16-bit scratch A
RAM_TMP16_B       == 0x0052    ; 16-bit scratch B
RAM_TMP8_A        == 0x0054    ; 8-bit scratch A
RAM_TMP8_B        == 0x0055    ; 8-bit scratch B

; --- 68HC11 On-Chip Hardware I/O Equates ($1000-$103F) ---
REG_PORTA         == 0x1000
REG_TCNT          == 0x100E
REG_TOC1          == 0x1016
REG_TMSK1         == 0x1022
REG_TFLG1         == 0x1023
REG_BAUD          == 0x102B
REG_SCCR1         == 0x102C
REG_SCCR2         == 0x102D
REG_SCSR          == 0x102E
REG_SCDR          == 0x102F
REG_ADCTL         == 0x1030
REG_ADR1          == 0x1031
REG_ADR2          == 0x1032
REG_ADR3          == 0x1033
REG_ADR4          == 0x1034

; --- Kernel Entry & Main Loop ---
        .org    0x8000

RESET_ENTRY:
        LDS     #0x01FF
        JSR     SCI_INIT
        JSR     MAIN_LOOP
        TPA
        ANDA    #0x7F           ; Clear CCR S bit so STOP halts CPU cleanly
        TAP
        STOP
RESET_HALT:
        BRA     RESET_HALT

MAIN_LOOP:
        BRCLR   *RAM_SIM_FLAGS, #0x01, SKIP_ADC_READ
        LDAA    REG_ADR1
        STAA    *RAM_MAP_RAW
        LDAA    REG_ADR2
        STAA    *RAM_BATT_RAW
        LDAA    REG_ADR3
        STAA    *RAM_CTS_RAW
        LDAA    REG_ADR4
        STAA    *RAM_TPS_RAW

SKIP_ADC_READ:
        JSR     CALC_FUEL_PW
        JSR     CALC_SPARK_ADVANCE
        JSR     CALC_DWELL_TICKS
        JSR     CALC_WASTEGATE_BOOST

        BRCLR   *RAM_CUTOUT_FLAGS, #0x04, SKIP_CUTOUT
        LDD     #0x0000
        STD     *RAM_FUEL_PW_HB
        CLRA
        STAA    *RAM_WG_DUTY

SKIP_CUTOUT:
        JSR     SCI_POLL_STEP
        RTS

; --- Subsystem Modules ---
        .include "fuel_tables.asm"
        .include "spark_tables.asm"
        .include "boost_vnt.asm"
        .include "sci_diag.asm"
        .include "vectors.asm"
