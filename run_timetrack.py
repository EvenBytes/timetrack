# Punto de entrada para PyInstaller (necesita un script, no un paquete con -m).
import sys

from timetrack.__main__ import main

sys.exit(main())
