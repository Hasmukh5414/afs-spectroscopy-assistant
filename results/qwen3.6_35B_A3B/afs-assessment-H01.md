# AFS technical assessment

Our existing NIR pilot follows API concentration in one powder formulation at
the tablet press feed frame. We are introducing a different API and excipient
blend and would like to reuse the calibration. A colleague suggests adding
Raman so that the old calibration will remain valid. What should our first
technical assessment and experiment be?

Evidence release: afs-official-v2

Source citations: present_with_gaps (not scientific verification)

Decision status: Further clarification needed

## Answer

Adding Raman spectroscopy does not preserve the validity of an existing NIR calibration for a different API/excipient blend. The previous NIR model is specific to its original formulation and will fail on new chemistry unless rebuilt or transferred with extensive cross-calibration, which is distinct from simply adding a second sensor [1]. While combining Raman and NIR can improve prediction accuracy and robustness compared to single-method models, this benefit applies to the *new* combined model trained on the new blend, not to the preservation of the old NIR-only calibration [2].

The first technical assessment should evaluate a multispectral approach (NIR + Raman) on the new blend to determine if the added complexity yields sufficient accuracy gains over a re-validated or rebuilt NIR model. This requires screening representative samples of the new API/excipient mix using both techniques in the feed frame geometry. [2, 3]

What is the acceptable prediction error (RMSEP) and update frequency for API concentration in the new blend, and do you have a reference method with known uncertainty to validate against?

## Full assessment

## Process requirements and constraints

- We are introducing a different API and excipient blend and would like to reuse the calibration.
  Customer: "Our existing NIR pilot follows API concentration in one powder formulation at the tablet press feed frame. We are introducing a different API and excipient blend and would like to reuse the calibration."
- A colleague suggests adding Raman so that the old calibration will remain valid.
  Customer: "A colleague suggests adding Raman so that the old calibration will remain valid."

## Approaches worth testing

### NIR-only (Rebuilt)

NIR is the most common online analysis method for pharmaceuticals due to its ability to penetrate dense solids and use standard fiber optics, making it ideal for feed frame monitoring. [1, 4]

NIR spectra contain overtones of -OH, -NH2, -CH groups common in organic compounds, providing structural information relevant to APIs and excipients. [4]

**Measurement / probe concept:** Diffuse reflection or transflection fiber probe in the feed frame, measuring into the powder bed. [4, 5]

**Limitations:**

- NIR bands are broad and overlapping; if the new API's functional groups are similar to excipients, selectivity may be insufficient without extensive calibration.
- Requires a new calibration set for the new blend; the old calibration is not transferable without significant effort and risk of error.

**Assumptions to confirm:**

- The new API/excipient blend contains sufficient NIR-active functional groups (e.g., C-H, O-H) with distinct spectral features from the matrix.

### Multispectral NIR + Raman

Combining Raman and NIR can predict API potency with higher accuracy and robustness than single-method models. [2]

Raman provides complementary structural information to IR, as peaks at the same wavenumber positions may have different intensities or appear only in one spectrum due to symmetry prohibitions. [8]

**Measurement / probe concept:** Compact multispectral probe (NIRaman) combining NIR reflection and Raman techniques in a single shaft, fitting into the tablet press feed frame. [2, 6, 7]

**Limitations:**

- Raman signals are weak (orders of magnitude weaker than IR absorption) and susceptible to fluorescence interference from organic compounds.
- Higher cost and complexity due to laser safety, specialized probes, and potential need for optical filters to eliminate luminescence.
- Requires careful alignment of NIR and Raman data streams and fusion algorithms.

**Assumptions to confirm:**

- The new API has Raman-active modes that are distinct from the excipients and not obscured by fluorescence.

## Important clarification questions

- What is the acceptable prediction error (RMSEP) and update frequency for API concentration in the new blend, and do you have a reference method with known uncertainty to validate against? — Determines whether a simpler NIR rebuild is sufficient or if the added cost/complexity of Raman is justified by accuracy gains.

## Shared risks and evidence gaps

- No information on the chemical structure of the new API/excipient blend to predict spectral distinctness.
- No data on current NIR model performance (RMSEP, bias) to establish a baseline for comparison.
- No confirmation of feed frame installation constraints (space, vibration, temperature) for potential Raman hardware.

## First feasibility study

Determine if a combined NIR-Raman model offers significantly better API prediction accuracy for the new blend than a rebuilt NIR-only model, justifying the added hardware complexity.

1. Collect representative powder samples of the new API/excipient blend at varying concentrations across the target range.
2. Measure each sample with both the existing NIR probe and a Raman probe in the feed frame geometry (or representative lab proxy if inline is not yet ready).
3. Obtain reference API concentrations using an independent validated method (e.g., HPLC) for each sample.

## Criteria to agree and check

- If the NIR-only model meets accuracy requirements, it is the simpler, lower-cost solution.
- If the NIR-only model fails but the NIR+Raman model succeeds with significant gain, the multispectral approach is justified.
- If neither model meets requirements, the issue may be fundamental to vibrational spectroscopy for this API (e.g., low Raman cross-section or NIR overlap) and requires method re-evaluation.

## Sources

1. S01 PDF pp. 14–15 — Multispectral Laboratory

   2. Pharmaceutical Manufacturing - In-line Monitoring of Blend Potency and Coating Processes A multispectral lab can be also focused on ensuring the quality and consistency of pharmaceutical products through in-line monit…

2. S01 PDF p. 15 — Multispectral Laboratory

   By combining Raman and NIR, these models can predict API potency or coating thickness with higher accuracy and robustness than single-method models.…

3. S01 PDF pp. 15–16 — Multispectral Laboratory

   Fig. 6. A multispectral NIRaman fiber probe has industrial grade design that combines NIR reflection and Raman optical techniques in a compact shaft that fits in a tablet press feed frame.…

4. S01 PDF p. 10 — Multispectral Laboratory

   5.2. Near-IR Spectrometry (includes FTIR, NIR-IR, or diffraction grating/slit spectrometer, Diffuse Reflection fiber probe, or Transmission fiber probe) Near-IR vibrational spectroscopy (NIR) is the most common online an…

5. S01 PDF pp. 4–5 — Multispectral Laboratory

   These differences in methods create natural limitations for their use, meaning that a given practical analysis task requires selecting the most optimal method and approach (technique) for its solution. For example, NIR s…

6. S01 PDF p. 11 — Multispectral Laboratory

   5.3. Raman Spectroscopy (The kit includes a Raman spectrometer, laser, and Raman fiber probe.) Measurement of the Raman scattering (RS) effect, is also a type of vibrational spectroscopy. Raman spectroscopy is based on r…

7. S01 PDF p. 11 — Multispectral Laboratory

   Therefore, using these methods in tandem provides more structural information about the molecules being studied than either method alone. From a technical standpoint, measuring Raman spectra is hampered by the low intens…

8. S01 PDF p. 11 — Multispectral Laboratory

   The Raman shift is used as the horizontal (spectral) axis in Raman spectroscopy, while the vertical axis shows the absolute intensity of the recorded signals (photon counts). Because Raman spectroscopy reflects energy tr…
