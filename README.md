# Duurtest

Stresstest voor de dispenser-units: de applicatie schakelt de voeding via een
STM32-relay, laat de units herhaald dispensen en doet er af en toe een
power cycle tussendoor.

## Installeren

```
pip install -r requirements.txt
```

## Starten

```
python main.py
```

## Structuur

```
main.py                  startpunt van de applicatie
logs/                    logbestanden, één per testrun
duurtest/
├── gui.py               leest het venster uit en toont de status
├── testDriver.py        de testloop, draait in een eigen thread
├── relay.py             seriële communicatie met de STM32-relay
├── config.py            alle instelbare waardes, op één plek
├── logsetup.py          zet de logging op
├── control_board.py     protocol van de machine (van de machine zelf)
├── vimbus.py            onderliggende VIMBus-laag (van de machine zelf)
└── Duurtest_GUI.ui      het venster, te bewerken in Qt Designer
```

De lagen praten maar één kant op: `gui.py` kent `testDriver.py`, en die kent
`relay.py` en `control_board.py`. De driver bevat geen Qt-code, dus je kunt hem ook zonder venster
draaien om te controleren of de hardware reageert:

```
python -m duurtest.testDriver
```

De instellingen die je normaal in het venster invult staan dan onderaan
`testDriver.py` hardgecodeerd.

## Logging

Elke testrun krijgt zijn eigen bestand in `logs/`, met het tijdstip waarop hij
begon in de naam:

```
logs/duurtest_2026-08-06_14-31-05.log
logs/duurtest_2026-08-06_15-02-48.log
```

Het bestand wordt geopend als je op Start drukt en gesloten als de test klaar
is, dus één bestand is precies één duurtest. Tussen runs door wordt er niets
weggeschreven.

Daarin staat alle communicatie met beide apparaten, met een tijdstempel tot op
de milliseconde:

```
2026-08-06 14:30:15.128  duurtest.relay       INFO    TX  ON
2026-08-06 14:30:15.140  duurtest.relay       INFO    RX  Relay is ON   (12 ms)
2026-08-06 14:30:25.191  duurtest.machine     INFO    TX  dispense_nl('CX01', 10000000)
2026-08-06 14:30:25.204  duurtest.machine     INFO    RX  dispense_nl -> {'result_code': ResultCode.OK}   (13 ms)
2026-08-06 14:30:25.205  duurtest.machine     ERROR   RX  dispense_all mislukt na 41 ms: Expected encrypted string ...
```

`TX` is wat de PC verstuurt, `RX` wat er terugkomt, met daarachter hoe lang het
duurde. De drie bronnen zijn te herkennen aan hun naam:

| Naam | Wat er gelogd wordt |
| --- | --- |
| `duurtest.relay` | seriële commando's naar de STM32 en zijn antwoorden |
| `duurtest.machine` | elk commando naar de control board en het antwoord |
| `duurtest.testDriver` | de test zelf: start, elke dispense, powercycles, fouten |

Wil je ook de ruwe bytes van de seriële poort zien, start dan met
`setup_logging(level=logging.DEBUG)` in `main.py`.

## Instellingen in het venster

| Instelling | Betekenis |
| --- | --- |
| Com-port relay | seriële poort van de relay/STM32 |
| Baudrate relay | baudrate van diezelfde poort, standaard 115200 |
| Com-port machine | seriële poort van de control board van de machine |
| Machine aan / uit | schakelt de voeding via de relay, zonder een test te starten |
| Number of dispenses | totaal aantal dispenses in de test |
| Power cycle interval | na elke N dispenses gaat de spanning eraf en weer aan, 0 = uit |
| Dispense amount | vaste hoeveelheid ml, of een willekeurige waarde tussen min en max; minimaal 0,8 ml, in stappen van 0,1 |
| Max units per dispense | 1 tot 6: per ronde doseren er zoveel units tegelijk, willekeurig getrokken tussen 1 en deze waarde |
| Unit List | welke units meedoen; per ronde worden daar willekeurig units uit gekozen |

Bij een willekeurige hoeveelheid trekt elke unit in een ronde zijn eigen waarde,
dus twee units die tegelijk doseren krijgen elk een andere hoeveelheid.

Na elke dispense vraagt de driver de machine met `get_status()` om zijn status
en wacht tot die weer `IDLE` meldt. Er wordt dus niet met een vaste
wachttijd gewerkt: een kleine dispense gaat meteen door, een grote krijgt de
tijd die hij nodig heeft.

Na een powercycle is de control board niet meteen terug: een oudere board
verdwijnt zonder spanning helemaal uit de lijst met com-poorten en Windows heeft
even nodig om hem opnieuw te herkennen. De driver blijft daarom elke seconde
opnieuw proberen te verbinden en gaat verder zodra dat lukt; pas na 30 seconden
geeft hij op met een foutmelding. Beide waardes staan in `config.py` als
`MACHINE_CONNECT_TIMEOUT` en `MACHINE_CONNECT_RETRY_INTERVAL`.

Soms komt de control board helemaal niet op na het inschakelen: hij verschijnt
niet als com-poort en zal dat ook niet meer doen tot hij opnieuw spanningsloos
is geweest. De driver doet dan wat je met de hand zou doen en zet de machine nog
een keer uit en aan, tot `POWER_CYCLE_ATTEMPTS` keer. Zet `RETRY_POWER_CYCLE` op
`False` om een run bij de eerste mislukking te laten stoppen. Hoe vaak dit
gebeurde staat aan het eind van de run in het log en in de melding.

Na elke ronde wordt gecontroleerd hoeveel er werkelijk uit is gekomen: het
vulniveau van elke unit wordt na afloop opnieuw gelezen en de daling vergeleken
met wat er gevraagd was. Dat komt zo in het log:

```
CX01: gevraagd 2.40 ml, gedispenst 2.38 ml (verschil -0.02 ml)
MH01: gevraagd 0.90 ml, gedispenst 1.65 ml (verschil +0.75 ml, buiten de tolerantie van 0.20 ml)
```

Een afwijking wordt gemeld en geteld, maar stopt de test nooit; het aantal staat
aan het eind in de melding. `DISPENSE_TOLERANCE_ML` bepaalt wanneer iets een
afwijking heet en met `CHECK_DISPENSED_AMOUNT = False` laat je de controle
helemaal weg.

De knop **Machine aan** schakelt de voeding zonder een test te starten. Dat is
nodig voor de oudere control boarden: die laten hun com-poort alleen zien als de
machine spanning heeft, dus zonder die knop valt de poort niet te kiezen. Bij de
nieuwe boarden blijft de poort altijd zichtbaar en is de knop alleen gemak.

De driver praat rechtstreeks met de control board van de machine over serieel,
via `control_board.py` en `vimbus.py`. Er zit geen xmlrpc-server meer tussen.

## Instellingen aanpassen

Alles wat ooit kan wijzigen staat in `duurtest/config.py`: de adressen van de
units, de verbindingsgegevens van de control board, de hoeveelheden, alle
wachttijden, en de opmaak van het logbestand. Waarde aanpassen, opslaan,
applicatie opnieuw starten. Wat je per run kiest hoort in het venster, niet
hier.
