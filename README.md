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
logs/                    logbestanden, één per keer dat je de applicatie start
duurtest/
├── gui.py               leest het venster uit en toont de status
├── testDriver.py        de testloop, draait in een eigen thread
├── relay.py             seriële communicatie met de STM32-relay
├── logsetup.py          zet de logging op
└── Duurtest_GUI.ui      het venster, te bewerken in Qt Designer
```

De lagen praten maar één kant op: `gui.py` kent `testDriver.py` en die kent
`relay.py`. De driver bevat geen Qt-code, dus je kunt hem ook zonder venster
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
2026-08-06 14:30:25.191  duurtest.dispenser   INFO    TX  prepareUnitForDispense('CX01', 192)
2026-08-06 14:30:25.204  duurtest.dispenser   INFO    RX  prepareUnitForDispense -> True   (13 ms)
2026-08-06 14:30:25.205  duurtest.dispenser   ERROR   RX  poll FOUT na 41 ms: Expected encrypted string ...
```

`TX` is wat de PC verstuurt, `RX` wat er terugkomt, met daarachter hoe lang het
duurde. De drie bronnen zijn te herkennen aan hun naam:

| Naam | Wat er gelogd wordt |
| --- | --- |
| `duurtest.relay` | seriële commando's naar de STM32 en zijn antwoorden |
| `duurtest.dispenser` | elke xmlrpc-aanroep naar de dispenser en het resultaat |
| `duurtest.testDriver` | de test zelf: start, elke dispense, powercycles, fouten |

Wil je ook de ruwe bytes van de seriële poort zien, start dan met
`setup_logging(level=logging.DEBUG)` in `main.py`.

## Instellingen in het venster

| Instelling | Betekenis |
| --- | --- |
| Com-port | seriële poort van de relay/STM32 |
| Baudrate | baudrate van diezelfde poort, standaard 115200 |
| Number of dispenses | totaal aantal dispenses in de test |
| Power cycle interval | na elke N dispenses gaat de spanning eraf en weer aan, 0 = uit |
| Dispense amount | vaste hoeveelheid ml, of een willekeurige waarde tussen min en max |
| Unit List | welke units meedoen; per dispense wordt er willekeurig één gekozen |

Na elke dispense vraagt de driver de unit met `UnitStatus()` om zijn status en
wacht tot die weer `IDLE` meldt. Er wordt dus niet met een vaste wachttijd
gewerkt: een kleine dispense gaat meteen door, een grote krijgt de tijd die
hij nodig heeft.

De dispenser zelf (de xmlrpc-server en zijn poort) staat als constante bovenin
`duurtest/testDriver.py`.
