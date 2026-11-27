; Cleanroom 68HC11 SCI Diagnostic Poll Loop & Interrupt Service Stubs

        .org    0xB000

; SCI_INIT: Configure 68HC11 SCI baud rate and enable TX/RX
SCI_INIT:
        LDAA    #0x30           ; Clean placeholder baud divisor (9600/62500)
        STAA    REG_BAUD
        LDAA    #0x00
        STAA    REG_SCCR1       ; 8-N-1 framing
        LDAA    #0x0C
        STAA    REG_SCCR2       ; Enable TE (bit 3) and RE (bit 2)
        RTS

; SCI_POLL_STEP: Non-blocking SCI poll; echoes MAP raw if RDRF (#0x20) is set
SCI_POLL_STEP:
        LDAA    REG_SCSR
        BITA    #0x20           ; Check RDRF
        BEQ     SCI_POLL_DONE
        LDAA    REG_SCDR
        STAA    *RAM_SCI_LAST_RX
        LDAA    *RAM_MAP_RAW
        STAA    REG_SCDR
        STAA    *RAM_SCI_LAST_TX
SCI_POLL_DONE:
        RTS

; Interrupt service routine stubs
SCI_ISR:
        RTI

TOC_ISR:
        RTI

ISR_NOP_RTI:
        RTI
