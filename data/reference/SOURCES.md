# Reference BOM sources and licenses

PartPilot's component data comes from public open-hardware designs. Everything else in the data set
(plants, suppliers, prices, lead times, stock lots, purchase orders, substitute records and their approval status,
internal part numbers) is **synthetic** and produced by `data/generate.py`.

| File | Design | Source | License of the source | What was taken |
|---|---|---|---|---|
| `uno_r3.csv` | Arduino UNO Rev3 (UNO-TH_Rev3e) | Arduino's official CAD files for A000066, linked from <https://docs.arduino.cc/hardware/uno-rev3/> | Creative Commons Attribution-ShareAlike 4.0 (stated in the `License.txt` inside the CAD archive) | Part list read from the Eagle schematic: reference designators, values, packages and the part numbers given there. Where the schematic gives only a value and footprint (e.g. `100n` / `0603`), PartPilot picked a real catalogue part number; the `source` column says so. |
| `pico.csv` | Raspberry Pi Pico R3 | `RPI-PICO_PCB-R3_BOM-R3-PUBLIC.xlsx` in the Pico design files, linked from <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html> | "made available openly, with no limitations" (permission to use, copy, modify and distribute, per the `LICENSE.txt` in the archive) | All fitted BOM lines with manufacturer part numbers. Pico H header and debug-connector lines are from the product description; their part numbers were picked by PartPilot. |
| `mk3s_plus.csv` | Original Prusa i3 MK3S+ (kit) | Printed-part names from <https://github.com/prusa3d/Original-Prusa-i3> (branch MK3S) | GNU GPL v2.0 (repository `LICENSE`) | Printed-part file names (e.g. `x-end-idler.stl`). Mechanical hardware, motors, hotend, electronics modules and their quantities are an **approximation** of the public kit assembly manual, written by hand and simplified; this is not an official Prusa BOM. |

The Einsy RAMBo mainboard is modelled as one bought module. Its design files are published by Ultimachine
(<https://github.com/ultimachine/Einsy-Rambo>) without a license file, so none of its BOM is copied here.

## License of the derived data

- `uno_r3.csv` is a derivative of a CC BY-SA 4.0 work and is shared under **CC BY-SA 4.0**
  (<https://creativecommons.org/licenses/by-sa/4.0/>). Attribution: Arduino UNO Rev3 reference design, Arduino.
- `pico.csv` follows the Raspberry Pi design-file terms quoted above.
- `mk3s_plus.csv` lists file names from a GPL-2.0 repository and hand-written approximations; it is shared under
  **GPL-2.0** to be safe.
- The generated tables in `data/raw/` inherit these terms for the rows that come from the files above.

"Arduino", "Raspberry Pi" and "Prusa" are trademarks of their owners. PartPilot is not affiliated with or endorsed by
any of them; the names are used only to identify the public designs the sample data is based on.
