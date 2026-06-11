# MyVolt / Voltera-like Serial Command Syntax

This document summarizes the protocol and workflows reverse-engineered from:

- `dump.txt`: raw serial traffic.
- `workflow1.txt`: software log for stop-heater, alignment, and start of probing.
- `calib.txt`: probing completion and dispenser calibration.
- `dispense_dots.txt`: dispensing one solder-paste dot.

The machine is not using transparent standard G-code. It uses a proprietary
line protocol with commands such as `V1`, `V3`, `V4`, `V102`, plus a few
standard-ish `M` commands.

## Line Framing

Commands sent to the machine are framed as:

```text
N<sequence> <payload> *<crc>,<length>
```

Example:

```text
N251 V201 E0 *c0,16
```

Fields:

- `N<sequence>`: monotonically increasing command number.
- `<payload>`: command and parameters, for example `V1 X10 Y20`.
- `*<crc>`: two lowercase hex digits in the observed logs.
- `,<length>`: decimal character count of the framed line up through the CRC,
  excluding the comma and the length itself.
- Online command and response lines are terminated with `0x0A` (`\n`) only.
  There is no carriage return (`\r`).
- The line terminator is not included in the CRC or length.

The space before `*` is significant. The CRC is calculated over:

```text
N<sequence> <payload> 
```

The serial baud rate is fixed at `250000`.

including that final trailing space.

## CRC Calculation

The checksum is CRC-8/MAXIM-Dallas:

- Width: 8 bits.
- Polynomial: `0x31` normal form, or `0x8c` reflected form.
- Initial value: `0x00`.
- Final XOR: `0x00`.
- Reflected input/output.

Python reference implementation:

```python
def crc8_maxim(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = ((crc >> 1) ^ 0x8C) & 0xFF
            else:
                crc = (crc >> 1) & 0xFF
    return crc


def frame_command(sequence: int, payload: str) -> str:
    prefix = f"N{sequence} {payload} "
    crc = crc8_maxim(prefix.encode("ascii"))
    body = f"{prefix}*{crc:02x}"
    return f"{body},{len(body)}"
```

Example:

```python
frame_command(251, "V201 E0")
# "N251 V201 E0 *c0,16"
```

All command frames recovered from the captures match this CRC and length rule.

## Response Behavior

Responses are asynchronous. Do not assume every status line belongs to the
immediately preceding command.

Common responses:

- `ok`: command accepted or completed enough for the planner.
- `empty`: planner/command queue empty.
- `positionUpdate x:... y:... z:... e:...`: current reported position.
- `probeMeasurement x:... y:... z:... displacement:... id:...`: probe result.
- `bedTemperatureUpdate current:... target:... timeRemaining:...`: heater telemetry.
- `homedStatusUpdate x:... y:... z:...`: homing state update.
- `toolUpdate type:... version:...`: controller-detected connected dispenser/tool type.
- `log: setting maximum Z position to ...`: current tool's maximum Z height.
- `log:` or plain log text: firmware/software status.
- `error:`: important. Treat as fatal even if a later `ok` appears.

`M400` is the main synchronization primitive. It often returns `ok`, `empty`,
and then a `positionUpdate`. Use it before depending on final position or before
starting a new high-level workflow phase.

For `homedStatusUpdate`, observed homed axes report `-1`. Axes reset to `0`
when tool preparation or homing state is cleared.

For `toolUpdate`, the controller reports the detected connected dispenser/tool.
Observed values include `Probe` and `Dispenser`. Probing should be attempted
only when the reported type is `Probe`.

During `V3 Z` preparation, the firmware may emit multiple
`Measure at switch: z-switch (z-min)` measurements. The last such measurement
seen before `Preparing tool -- completed ...` is the observed tool offset for
the current tool. The same preparation normally emits
`setting maximum Z position to ...`; use the last observed value as the maximum
Z limit for that installed tool. Any explicit move toward this limit should
target a smaller value; the GUI uses a `0.5 mm` margin.

Important caveat: one dispenser calibration log shows `V3 Z` emitting `error:`
messages and still returning `ok`. A robust sender must scan all responses
between commands and fail on `error:`.

## Coordinate / Motion Notes

Observed coordinate conventions:

- `V1 X0 Y0` is a park/origin move, not a home command.
- X travel range is `0` to `128 mm`.
- Y travel range is `0` to `157 mm`.
- Positive X moves toward the left.
- Positive Y moves toward the bottom.
- Homing is hidden inside `V3 Z` or explicitly invoked with `V5`.
- Z top/max position is around 9 to 11 mm depending on tool calibration.
- The z-switch XY return position observed in logs is
  `X4.820494 Y7.966725`.
- Dispensing and probing near the substrate use negative Z coordinates.
- The `D` flag enables compensation/offset behavior for Z and XYZ moves.
- With `V102 Z0.1`, reported final Z for `D` moves is commonly about
  `+0.1 mm` above the commanded compensated Z. This indicates `V102 Z...`
  configures the dispenser hover height during a write/dispense operation.

The observed dot routine has sub-0.02 mm diagonal XY motion. That is likely
below practical machine resolution and can be ignored for a simplified dot
implementation. The extrusion/plunger pulse is the important part.

## User Stage Region and Height-Map Probing

The top `40 mm` of the stage is treated as calibration area. User work regions
must stay inside:

```text
X: 0..128 mm
Y: 40..157 mm
```

For height-map detection, probe an evenly spaced grid or explicit point list
inside the user region. The stored height-map value is the probed
`probeMeasurement z` value at each point. No extra software correction between
probe and dispenser offsets is required if the controller's built-in tool offset
compensation is active.

Observed/inferred height-map sequence:

```text
V201 E1
V1 X<x0> Y<y0>
M400
V4 R1 Iheightmap_1
V4 R0.1 Iheightmap_1
V1 X<x1> Y<y1>
M400
V4 R0.1 Iheightmap_2
...
V201 E0
V1 Z<maximum-z-minus-0.5>
V1 X4.820494 Y7.966725
M400
```

Use `V4 R1` only for first-point coarse positioning. Keep the first point only
after the following `V4 R0.1`; all remaining height-map points use `V4 R0.1`
directly. If no maximum Z has been reported yet, use `V3 Z` as the fallback
final retract/return command.

## Command Table

The meanings below are based on observed logs only.

| Command | Syntax | Meaning |
|---|---|---|
| `M141` | `M141 T<temp> D<seconds>` | Start bed/heater to target temperature. `T` is target temperature in C. `D` is required and appears to be a duration/timeout in seconds. Observed maximum is `D3600`. |
| `M142` | `M142` | Stop bed/heater. Sets target temperature to `0.0`. |
| `M18` | `M18` | Disable motors and/or reset tool preparation and homed state. Motor-off logs may be asynchronous. |
| `M400` | `M400` | Wait/synchronize until queued moves complete. Often followed by `empty` and `positionUpdate`. |
| `V1` | `V1 X... Y... [Z...] [D] [E...] [F...]` | Main absolute motion and extrusion command. |
| `V2` | `V2 X... Y...` | Relative XY jog. Used for alignment/fine moves. |
| `V3` | `V3 Z` | Prepare current tool if needed; otherwise retract/ensure safe/top Z. |
| `V4` | `V4 [R<clearance>] [I<id>]` | Probe current XY and report `probeMeasurement`. `R` is best interpreted as post-probe retract/clearance. |
| `V5` | `V5` | Home/reset X and Y only. Resets tool preparations, homes Y then X. Does not perform full Z/tool calibration. |
| `V101` | `V101 D1` | Select dispenser tool in observed logs. Do not use this to select the probe; the controller reports detected hardware via `toolUpdate`. |
| `V102` | `V102 [Z<height>]` | Set or clear dispenser hover height for write/dispense moves. `V102 Z0.1` sets hover height; bare `V102` clears/ends that mode. |
| `V201` | `V201 E0` or `V201 E1` | Disable/enable probe height safety. |

## Command Details

### `M141` - Start Heating

Observed:

```text
M141 T240 D300
```

Response:

```text
New target Temperature: 240.000000
bedTemperatureUpdate current:... target:240.0 timeRemaining:...
```

Likely use:

```text
M141 T240 D300
M400
```

`D` is required by the controller. It appears to be a duration or timeout in
seconds. The proprietary software used `D300`, and the observed maximum is
`D3600` (1 hour).

### `M142` - Stop Heating

Observed:

```text
M142
```

Response:

```text
New target Temperature: 0.000000
bedTemperatureUpdate current:... target:0.0 timeRemaining:0.0
```

### `M18` - Disable Motors / Reset Preparation

Observed in stop-heating and parking:

```text
M18
```

Responses include:

```text
Reset tool preparations
set X-axis homed state to 0
set Y-axis homed state to 0
X-axis motor was turned off
Y-axis motor was turned off
Z-axis motor was turned off
E-axis motor was turned off
```

The exact set of axes reported depends on current state.

### `M400` - Synchronize

Use this after motion commands when final position matters:

```text
V1 X94 Y129
M400
```

Typical response:

```text
ok
empty
positionUpdate x:94.000000 y:129.000000 z:... e:...
```

### `V1` - Main Motion / Extrusion

Observed option letters:

- `X`: absolute X coordinate.
- `Y`: absolute Y coordinate.
- `Z`: absolute Z coordinate.
- `D`: compensation/dispense-height flag.
- `E`: relative extrusion/plunger amount.
- `F`: feedrate.

Observed forms:

```text
V1 X86.5 Y110.2
V1 X0 Y0
V1 Z-0.981107
V1 Z-0.981107 D
V1 Z-1.98106 D E0.095 F244.13
V1 X86.397551 Y76.502449 Z-1.981079 D E0.005 F244.13
V1 X86.3925 Y76.5075 Z-1.86106 D F200
V1 E0.01
```

Interpretation:

- X/Y/Z are absolute targets.
- `E` is relative. Positive values push material; negative values retract.
- `D` causes the firmware to apply substrate/probe/dispenser compensation.
- `F` is speed/feedrate. Observed values include `F200`, `F244.13`,
  `F500`, and `F519.23`.
- `V1 E...` alone increments the reported `e` position without XYZ motion.

### `V2` - Relative XY Jog

Observed forms:

```text
V2 X0 Y-2
V2 X2 Y0
V2 X0 Y-0.05
V2 X0 Y0.05
```

This moves relative to the current XY position. It was used during alignment
and fine positioning.

### `V3 Z` - Prepare Tool / Max-Z Retract

For the probe tool, first use performs a full preparation:

- Homes Y.
- Homes X.
- Homes Z.
- Moves to XY positioner.
- Measures calibration plate and tool/probe trigger position.
- Computes probe displacement.
- Measures top Z and Z switch.
- Sets maximum Z position.

For the dispenser tool, first use performs dispenser preparation:

- Homes Z.
- Moves to XY positioner.
- Measures top Z and Z switch.
- Sets maximum Z position.

After the tool is already prepared, `V3 Z` usually acts as a max/top-Z retract
and return toward the z-switch position.

Failure caveat:

```text
V3 Z
< error: Unable to move to z-switch (z-min), switch did not trigger
< error: Unable to home z-axis, could not measurez-switch (z-min)
< ok
```

Scan for `error:` and do not trust `ok` alone.

### `V4` - Probe

Observed forms:

```text
V4 R0.2
V4
V4 R1 Ipoint/x29.0000,y64.0000
V4 R3 Ipoint/x99.0000,y134.0000
```

Response:

```text
probeMeasurement x:... y:... z:... displacement:... samplesTaken:1 touchesUsed:1 id:...
```

Options:

- `R<value>`: best interpreted as post-probe retract/clearance. Observed
  values are `0.2`, `1`, and `3`.
- `I<id>`: string identifier for the probed point. The software uses
  `Ipoint/x...,y...`.

Observed uses:

- `V4 R0.2`: lower/probe at alignment target and remain near the pad.
- Bare `V4`: measure alignment target with default clearance.
- `V4 R1 Ipoint/...`: substrate probing grid in `calib.txt`.
- `V4 R3 Ipoint/...`: probing grid in `dump.txt`; likely larger clearance.

### `V5` - Home XY

Observed:

```text
V5
```

Responses:

```text
set X-axis homed state to 0
set Y-axis homed state to 0
Reset tool preparations
homing axis: Y
Measure at switch: back (y-min)
homing axis: X
Measure at switch: right (x-min)
ok
```

Use this when explicit XY homing is needed before tool selection/preparation.

### `V101 D1` - Select Dispenser

Observed:

```text
V101 D1
```

Response:

```text
Reset tool preparations
toolUpdate type:Dispenser version:1
ok
```

Then usually:

```text
M400
V3 Z
```

The dispenser logs prove `V101 D1` selects the dispenser. Do not use `V101` as a
probe-selection command. The hardware reports the detected connected tool via
`toolUpdate`; probing should be attempted only when that reported type is
`Probe`.

### `V102` - Dispenser Hover Height

Observed:

```text
V102 Z0.1
...
V102
```

`V102 Z0.1` is sent before dispensing and calibration paths. Bare `V102` is
sent after the dispense/calibration path and before retract/park.

Current interpretation:

- `V102 Z<height>` sets the dispenser hover height used by later write/dispense
  moves, especially `D` compensated moves.
- Bare `V102` clears or finalizes that hover-height mode.

### `V201` - Probe Height Safety

Observed:

```text
V201 E1
V201 E0
```

Responses:

```text
Probe
  displacement: ...
  height safety: ON, safe height is ...
```

or:

```text
Probe
  displacement: ...
  height safety: inactive
```

`E1` enables height safety. `E0` disables it. In one log, safe height was
reported as maximum Z minus 1 mm.

## Proprietary Software Workflows

The following are the observed high-level sequences.

### Stop Heating and Park

Observed in `workflow1.txt`:

```text
M142
M18
M400
```

Purpose:

- Turn off heater.
- Reset tool preparation and/or disable motors.
- Drain queue and receive final status updates.

### Alignment / Locate Pads

Observed sequence:

```text
V3 Z
V1 X94 Y129
M400
V4 R0.2
M400
V4
V3 Z
M400

V3 Z
V1 X34 Y69
M400
V4 R0.2
M400
V4
V3 Z
M400

V3 Z
V1 X0 Y0
M400
```

Purpose:

1. Prepare probe and home/calibrate if needed.
2. Move to first alignment pad.
3. Probe/lower with small clearance (`R0.2`).
4. Measure with bare `V4`.
5. Retract to safe/top Z.
6. Repeat for second alignment pad.
7. Park at `X0 Y0`.

### Substrate / Board Probing

Start observed in `workflow1.txt` and continuation in `calib.txt`:

```text
V3 Z
V201 E1
V1 X29 Y64
V4 R1 Ipoint/x29.0000,y64.0000
V1 X... Y...
V4 R1 Ipoint/x...,y...
...
V201 E0
V3 Z
V1 X0 Y0
M400
```

Purpose:

- Ensure probe is prepared and at safe Z.
- Enable height safety.
- Visit a grid or list of substrate points.
- Probe each point and tag it with `Ipoint/x...,y...`.
- Disable height safety.
- Retract and park.

Each `V4` returns a `probeMeasurement` containing the measured Z for the
height map. The software then uses the height map for later `D` compensated
dispense moves.

### Dispenser Tool Setup / Calibration Start

Observed in `calib.txt`:

```text
V5
M400
V101 D1
M400
V3 Z
V102 Z0.1
```

Purpose:

- Explicitly home XY.
- Select dispenser.
- Prepare dispenser tool and calibrate Z/top travel.
- Set dispenser hover height before drawing calibration features.

If `V3 Z` fails, the proprietary software falls back to explicit homing and
tool selection. Error lines must be handled.

### Dispenser Calibration Stroke

Calibration paths are drawn using the same primitive as normal dispensing.
A representative stroke:

```text
V1 X70.731 Y96.612513
V1 Z-0.952906 D
V1 Z-1.824334 D F200
V1 Z-1.952906 D E0.09 F244.13
V1 X70.716714 Y96.612513 Z-1.952888 D E0.01 F244.13
V1 X57.239399 Y96.612513 Z-1.93931 D F200
V1 X57.2182 Y96.612513 Z-1.939305 D E-0.014839 F244.13
V1 Z-1.819179 D E-0.084088 F244.13
V1 Z-1.909305 D F200
V1 X57.5182 Y96.612513 Z-1.889375 D F200
V1 Z-0.939375 D F200
V1 Z-0.939375
M400
```

Motion intent:

1. Move to stroke start.
2. Approach with `D` compensation.
3. Prime/plunge with positive `E`.
4. Move a small start segment with small positive `E`.
5. Draw the main line at fixed flow/pressure with no additional `E`.
6. Retract pressure with negative `E`.
7. Lift and clear.
8. Synchronize.

End of calibration:

```text
V102
V3 Z
V1 X0 Y0
M400
```

### Generic Printed Trace

A printed trace uses `V102` to set the compensated hover/travel offset and
`V1 ... D` moves whose explicit `Z` value is the height-map value at that XY
position. Do not add the hover height to the commanded `Z`; doing so would
double-apply the offset.

Before building and sending a print path, prepare the installed tool and wait
for synchronization. This normally emits `setting maximum Z position to ...`,
which should be cached for the final retract/return.

```text
V3 Z
M400
```

Before the first trace chunk, clear any existing hover offset and move XY
directly from the prepared z-switch/max-Z state to the trace start. Omit `F` so
the controller uses its direct/default speed.

```text
V102
V1 X<start-x> Y<start-y>
```

For each trace chunk:

```text
V102 Z<travel-height>
V1 Z<height-map-z-at-start> D F<speed>
V1 X<start-x> Y<start-y> Z<height-map-z-at-start> D F<speed>
V102 Z<print-height>
V1 X<start-x> Y<start-y> Z<height-map-z-at-start> D F<speed>
V1 E<kick>
V1 X<next-x> Y<next-y> Z<height-map-z-at-next> D F<speed>
...
V1 E-<retract>
V102
V102 Z<travel-height>
V1 Z<height-map-z-at-end> D F<speed>
V102
```

Long paths can be split into chunks. Each chunk gets its own positive kick at
the start and negative retract at the end. After the final chunk, clear `V102`,
raise to a value strictly below the last observed maximum Z, move back to the
z-switch XY position, then use `M400` to synchronize the queued print path. The
GUI currently uses `maximum-z - 0.5 mm`.

```text
V102
V1 Z<maximum-z-minus-0.5>
V1 X4.820494 Y7.966725
M400
```

### Single Dot Dispense

Observed in `dispense_dots.txt`:

```text
V3 Z
V102 Z0.1
V1 X86.3925 Y76.5075
V1 Z-0.98106 D
V1 Z-1.845346 D F200
V1 Z-1.98106 D E0.095 F244.13
V1 X86.397551 Y76.502449 Z-1.981079 D E0.005 F244.13
V1 X86.4075 Y76.4925 Z-1.981115 D E-0.009849 F244.13
V1 Z-1.852331 D E-0.090149 F244.13
V1 Z-1.881115 D F200
V1 X86.3925 Y76.5075 Z-1.86106 D F200
V1 X86.4075 Y76.4925 Z-1.861115 D F200
...
V1 Z-0.981107 D F200
V1 Z-0.981107
M400
V102
V3 Z
V1 X0 Y0
M400
```

Motion intent:

1. Prepare dispenser and set hover height with `V102 Z0.1`.
2. Move to the dot location.
3. Approach to hover height.
4. Move down near substrate.
5. Apply a pressure pulse: positive `E0.095`, then small positive `E0.005`.
6. Retract nearly the same plunger amount with negative `E`.
7. Lift to a safer height.
8. Optional tiny XY shaping/wipe motion follows. This is likely below machine
   resolution and can be ignored for a simplified implementation.
9. Clear `V102`, retract to top Z, and park.

Important extrusion observation:

```text
0.095 + 0.005 - 0.009849 - 0.090149 ~= 0
```

The dot is produced by a temporary pressure pulse, not by leaving the plunger
advanced. A simplified dot should preserve this positive-pulse then negative-
retract pattern.

### Heating

Start heating:

```text
M141 T240 D300
M400
```

`M141` requires a `D` duration field. The observed maximum is `3600` seconds.

Stop heating:

```text
M142
```

Monitor:

```text
bedTemperatureUpdate current:<current> target:<target> timeRemaining:<seconds>
```

Do not assume a normal 3D-printer `M140/M190` interface; the observed commands
are `M141` and `M142`.

## Practical Controller Guidance

1. Maintain command sequence numbers and frame every payload with CRC and length.
2. Wait for `ok`, but also scan all response lines for `error:`.
3. Use `M400` as a barrier before reading final `positionUpdate`.
4. Expect telemetry to arrive between command responses.
5. Pace long command streams. Do not dump large print paths into the serial port
   as a burst; wait for `ok` or an error before sending the next framed command.
   Otherwise the controller can report line-length errors such as
   `Missing characters detected`.
6. Treat `V3 Z` as a stateful prepare/retract command whose behavior depends on
   active tool and preparation state.
7. Use `V5` and `V101 D1` before dispenser operations if tool state is unknown.
8. Use `V201 E1` only during probing workflows, and `V201 E0` before leaving
   probing.
9. Use `V102 Z...` to set dispenser hover height before write/dispense moves
   and bare `V102` afterward.
10. For dispensing, preserve the prime/retract `E` balance. Net `E` for dots may
   be near zero even though material is deposited.
11. Do not send ordinary `G1`, `G28`, `M140`, or `M190` unless separately tested;
    they were not observed in these captures.

## Unknowns / Open Items

- Exact semantics of `M141 D...` beyond "required duration/timeout-like field"
  are not proven.
- Exact internal meaning of `D` is inferred as compensation/dispense-height mode.
- Exact internal behavior of `V102` is inferred as dispenser hover-height mode.
- The relationship between `R` in `V4 R...` and final Z can be affected by
  probe displacement, safety height, and current state.
- Coordinate transforms after alignment are likely handled in software before
  commands are emitted; the controller sees transformed absolute coordinates.
