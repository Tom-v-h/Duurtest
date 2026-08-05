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
duurtest/
├── gui.py               leest het venster uit en toont de status
├── testDriver.py        de testloop, draait in een eigen thread
├── relay.py             seriële communicatie met de STM32-relay
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

## Instellingen in het venster

| Instelling | Betekenis |
| --- | --- |
| Com-port | seriële poort van de relay/STM32 |
| Baudrate | baudrate van diezelfde poort, standaard 115200 |
| Number of dispenses | totaal aantal dispenses in de test |
| Power cycle interval | na elke N dispenses gaat de spanning eraf en weer aan, 0 = uit |
| Dispense amount | vaste hoeveelheid ml, of een willekeurige waarde tussen min en max |
| Unit List | welke units meedoen; per dispense wordt er willekeurig één gekozen |

De dispenser zelf (xmlrpc-server op COM12) staat als constante bovenin
`duurtest/testDriver.py`.
