# boot.py - v1.0.6
import usb_cdc
import supervisor
import storage
import microcontroller
import usb_midi

# optional
# supervisor.set_next_stack_limit(4096 + 4096)
usb_cdc.enable(console=True, data=True)

# Turn off CircuitPython's USB MIDI device. MacroForge doesn't use it, and on
# PCs with Intel Smart Sound Technology it loads Intel's USB audio driver
# (IntcUSB.sys), which can blue-screen with DRIVER_IRQL_NOT_LESS_OR_EQUAL.
usb_midi.disable()

# used to identify pog compatible keyboards while scanning com ports
supervisor.set_usb_identification("Pog", "Pog Keyboard")

# index configs
# 0 - show usb drive | 0 false, 1 true
if microcontroller.nvm[0] == 0:
    storage.disable_usb_drive()
    storage.remount("/", False)
