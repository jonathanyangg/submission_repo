# Example orbit

Robot 0 starts at the origin, holds still, and broadcasts its pose with a blue LED.
Robot 1 starts 0.8 m to the east, facing north, and orbits the broadcast pose at about 0.5 m.
The steady radius in the headless simulator is about 0.53 m. That offset is the proportional controller, not a pose-file error.
The LED on robot 1 changes with the angle around robot 0.

Both start poses are inside the play field and 0.8 m apart. Wheel commands stay within ±50 and are sent every loop.

This folder is a valid submission. It is here so the checks have a known-good input. A real experiment should use a new directory such as `submissions/<your-name>/` rather than editing this one.
