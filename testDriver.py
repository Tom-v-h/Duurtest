from relay import RelayController
import time
import xmlrpc.client
import random


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