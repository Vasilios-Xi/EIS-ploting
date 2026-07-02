---
name: eis-impedance-plotting
description: Safely parse Metrohm Autolab NOVA .nox electrochemical impedance data, normalize impedance by electrode area, and automate Origin to create standardized editable Nyquist plots. Use when the user asks to process NOX EIS files, perform area normalization, draw impedance/Nyquist plots in Origin, generate an original EIS plot, or generate the publication-ready plot shifted to the leftmost Y=0 intercept. Require the user to provide the active electrode area A in cm² and the output directory before running.
---

# 阻抗图绘制

## Required inputs

Obtain all three values before running:

1. NOX file or folder path.
2. Active electrode area `A` in `cm²`; require a finite positive number and never infer it.
3. Output directory path; never choose or overwrite an unrelated directory.

Treat the sample label as optional. Infer it from the text before the first `-` or `_` in the first NOX filename when omitted.

## Run the workflow

Use the bundled deterministic workflow:

```powershell
py scripts/run_eis_workflow.py "<NOX file or folder>" `
  --area-cm2 <A> `
  --output-dir "<output directory>" `
  --sample-label "<optional sample label>"
```

Resolve `scripts/` relative to this skill directory. The workflow requires Windows, a locally installed Origin, and the official `originpro` Python package. If Origin automation is unavailable, report the dependency problem; do not replace OPJU output with another plotting backend.

## Processing rules

- Parse MS-NRBF records without importing, instantiating, or executing serialized .NET types.
- Extract `Frequency`, `H_Real`, `H_Imaginary`, `H_Modulus`, `H_Argument`, `H_Phase`, and `Time`.
- Deduplicate identical complete signal groups.
- Calculate `Z_real_area_Ohm_cm2 = H_Real × A`.
- Calculate `minus_Z_imag_area_Ohm_cm2 = H_Imaginary × A`; NOVA supplies the displayed `-Z″` value, so do not invert its sign again.
- Preserve the measured first point in the CSV and original plot; do not use first-point zeroing.
- For the final plot only, linearly interpolate each curve's leftmost `Y=0` crossing, shift that crossing to `(0,0)`, and exclude earlier negative-Y points.

## Plot specification

- Generate both the original area-normalized plot and the final intercept-shifted plot.
- Use an 8 × 6.3 inch canvas.
- Set the layer to 14 × 10 cm, left margin 3 cm, top margin 2.7 cm.
- Use 22 pt axis titles, 20 pt tick labels, and 2 pt axis lines.
- Use filled circle markers, solid connecting lines, and the blue-to-red series palette.
- Use identical X/Y ranges and major increments; choose a nice common range that covers all plotted data.
- Keep the top and right axis lines but remove their major and minor ticks.
- Place the legend immediately inside the right axis line with a visible nonzero internal gap; never let the rendered legend bounds touch/cross the right axis line or leave the graph layer.
- After placing the legend, verify that the legend and sample label do not overlap; if their rendered bounding boxes overlap, move the sample label downward until they are separated while keeping both objects inside the layer.
- Parse names such as `-5O2` as `5% O₂` and sort those series numerically.
- Export PNG at 1800 px width.

## Allowed outputs

Create only:

```text
<output directory>/
├─ NOX处理结果/   # one UTF-8-BOM area-normalized CSV per EIS dataset
├─ 原始EIS图/     # one OPJU and one PNG
└─ 最终EIS图/     # one OPJU and one PNG
```

Do not generate JSON, PDF, SVG, XLSX, summary tables, or auxiliary documentation unless the user explicitly requests them.

## Verify

After execution:

1. Confirm every NOX file produced at least one nonempty CSV.
2. Confirm all normalized values are finite and use the supplied `A`.
3. Confirm both OPJU files and both PNG files exist.
4. Inspect both PNGs for clipping, correct legend order, a legend tight to but not touching/crossing the right axis line, no legend/sample-label overlap, and no top/right ticks.
5. Confirm the final curves begin at the interpolated leftmost X-axis intercept rather than at the acquisition first point.
