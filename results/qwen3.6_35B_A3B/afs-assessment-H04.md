# AFS technical assessment

We want to investigate water content in a petroleum line using MIR ATR. The
analyzer would be in another building. We already have a silica fiber cable
from a visible or NIR measurement setup and would like to reuse it. The link
length, routing and operating conditions have not yet been supplied. Can the
assistant approve this concept?

Evidence release: afs-official-v2

Source citations: present_with_gaps (not scientific verification)

Decision status: Further clarification needed

## Answer

The concept of using MIR ATR for water content in petroleum is technically valid, as MIR absorption bands provide high sensitivity for this analyte [1] [2]. However, the reuse of an existing visible/NIR silica fiber cable is not approved because standard silica fibers exhibit steep optical loss beyond 2.0 µm, which cuts off the critical MIR water bands [3]. The slide showing ATR probes in a furnace confirms probe material compatibility but does not extend to fiber transmission limits [1].

The inspected evidence does not support the transmission of MIR signals (beyond ~2.0 µm) over long distances using standard silica fibers; chalcogenide, polycrystalline, or hollow waveguide fibers are required for this range [3]. Additionally, the specific operating conditions (temperature, pressure) and cable length for the remote analyzer location are unknown, which are critical for selecting the appropriate fiber type and probe sealing.

## Full assessment

## Process requirements and constraints

- Investigate water content in a petroleum line using MIR ATR.
  Customer: "We want to investigate water content in a petroleum line using MIR ATR."
- The analyzer would be in another building, requiring a long cable link.
  Customer: "The analyzer would be in another building."
- Reuse an existing silica fiber cable from a visible or NIR measurement setup.
  Customer: "We already have a silica fiber cable from a visible or NIR measurement setup and would like to reuse it."

## Important clarification questions

- What is the maximum cable length and the operating temperature/pressure at the analyzer location? — Selection of fiber type (silica vs. MIR-specific) and probe sealing material.
- What is the target water concentration range and the required detection limit? — Choice of spectral window and sensitivity requirements for the detector/probe.

## Shared risks and evidence gaps

- Standard silica fibers have high loss beyond 2.0 µm, making them unsuitable for MIR water bands without specialized fiber types.
- Probe sealing and material compatibility with petroleum and process conditions is not yet defined.

## First feasibility study

Determine if a specific MIR fiber link can deliver sufficient signal for water quantification at the target concentration in the actual process environment.

1. Obtain a sample of the petroleum line fluid with known water content.
2. Connect a candidate MIR fiber (chalcogenide or hollow waveguide) of the required length to an ATR probe.
3. Measure the spectrum in the lab and compare signal intensity at water bands against a reference method.
4. If feasible, test the setup under simulated process conditions (temperature/pressure).

## Criteria to agree and check

- Signal-to-noise ratio is sufficient for the required detection limit.
- The fiber transmission loss over the specified length does not degrade the spectrum below usable levels.
- The probe sealing material remains compatible with the process fluid and conditions.

## Sources

1. S02 PDF p. 13 — AFS Process Control Slides

   Slide 13 (transcribed): Application: measurement of water content in petroleum. Printed statement: "ATR-Probes with ATR-tips from ZrO2 (& Diamond, etc.) sealed in PEEK-shaft and protected by PEEK-tubing can be used in mi…

2. S01 PDF pp. 9–10 — Multispectral Laboratory

   5.1. Mid-IR Spectrometry (includes FTIR Mid-IR spectrometer, ATR fiber probe) Vibrational spectroscopy in the mid-IR range (4000-400 cm-1) is one of the most informative methods of chemical analysis. Due to the high exti…

3. S02 PDF p. 7 — AFS Process Control Slides

   Slide 7 (transcribed): Spectral Vis-NIR cables for industrial process control. Printed statement: "Robust 100-300m cables made with Silica fibers of low OH-content (<0.1ppm OH) enable remote industrial process-control." …
