# Rear Axle Steering – Synthetic Software Requirements (Fixture Set A)

This document is synthetic reference material. It contains no customer data.

## 1 Normal operation

RAS-SW-REQ-0001: The rear axle steering shall follow the commanded rear wheel
angle with a steady-state deviation of at most 0.2 deg at a vehicle speed of
50 km/h.

RAS-SW-REQ-0002: Bei einer Fahrzeuggeschwindigkeit unterhalb von 10 km/h muss
die Hinterachslenkung einen Lenkwinkel von bis zu 10 deg gegensinnig zur
Vorderachse stellen.

## 2 Grenzwerte / Boundaries

RAS-SW-REQ-0003: The rear wheel angle shall not fall below the lower limit of
-10.0 deg in any operating mode.

RAS-SW-REQ-0004: Der Hinterradwinkel darf den oberen Grenzwert von +10.0 deg
in keinem Betriebsmodus ueberschreiten.

## 3 Zeitliche Bedingungen

RAS-SW-REQ-0005: After detection of an actuator position sensor fault the
system shall enter the degraded state within 100 ms and set DTC C1A27.

## 4 Mehrfachbedingungen

RAS-SW-REQ-0006: Wenn die Fahrzeuggeschwindigkeit groesser als 80 km/h ist und
die Gierrate 5 deg/s ueberschreitet, muss der Hinterradwinkel auf maximal
2.0 deg begrenzt werden.

## 5 Unvollstaendige Vorgabe

RAS-SW-REQ-0007: The system shall limit the rear wheel angle to a safe value
when the vehicle is driving fast.

## 6 Widersprechende Vorgaben

RAS-SW-REQ-0008: The rear axle steering shall remain fully available after a
single CAN message loss.

RAS-SW-REQ-0009: After a single CAN message loss the rear axle steering shall
immediately deactivate and hold the rear wheel angle at 0 deg.

## 7 Anforderung ohne Kennung

The diagnostic session shall be terminated when the ignition is switched off.
