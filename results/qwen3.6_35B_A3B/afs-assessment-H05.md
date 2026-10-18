# AFS technical assessment

We have a liquid process that occasionally goes off specification. We suspect
contamination, but we cannot yet name the contaminant or the affected quality
variable. We would like you to recommend the best spectroscopy method and
exact sensor configuration immediately. What information and first experiment
should come before that decision?

Evidence release: afs-official-v2

Source citations: present_with_gaps (not scientific verification)

Decision status: Further clarification needed

## Answer

The customer's process is a liquid system with occasional off-specification events due to suspected but unidentified contamination. The primary goal is monitoring this process, but the specific analyte and quality variable are not yet defined. This lack of target definition prevents selecting a single optimal spectroscopy method immediately, as different contaminants require different spectral signatures (e.g., vibrational vs. electronic transitions). [1]

NIR spectroscopy is a strong candidate for initial screening because it is the most common online analysis method using fiber-optic probes and can detect overtones of -OH, -NH2, -CH groups found in many organic compounds [2] [3]. It allows for penetration depths of 1-10 mm in liquids/colloids with average sensitivity/selectivity [4]. Raman spectroscopy is another candidate, offering high selectivity for specific molecular fingerprints via Stokes shift, but it has high cost and requires laser excitation [5] [6]. UV/Vis absorption has low selectivity for complex mixtures due to signal overlap [7] [8].

Before committing to a specific sensor configuration, we recommend a representative laboratory screening study. This study should test both NIR and Raman probes on the actual process liquid under controlled contamination scenarios (spiking with likely contaminants) to determine which method provides sufficient signal-to-noise for the unknown analytes. The study must include independent reference measurements (e.g., HPLC/GC-MS) to validate spectral predictions. [1, 9]

What are the physical and chemical properties of the suspected contaminants (e.g., organic/inorganic, polar/non-polar, concentration range)?

What is the acceptable response time for process control or alerting?

What is the available budget range for the spectroscopic system and its integration?

Without knowing the target analyte, no single method can be guaranteed to work. Laboratory findings are not production approval; installation feasibility depends on process compatibility (cleaning, pressure, temperature) which is unknown.

## Full assessment

## Process requirements and constraints

- We have a liquid process that occasionally goes off specification. We suspect contamination, but we cannot yet name the contaminant or the affected quality variable.
  Customer: "We have a liquid process that occasionally goes off specification. We suspect contamination, but we cannot yet name the contaminant or the affected quality variable."
- We would like you to recommend the best spectroscopy method and exact sensor configuration immediately.
  Customer: "We would like you to recommend the best spectroscopy method and exact sensor configuration immediately."

## Approaches worth testing

### NIR Spectroscopy

NIR is the most common online analysis method using fiber-optic probes, detecting overtones of -OH, -NH2, -CH groups in organic/inorganic compounds. [2]

NIR allows penetration depths of 1-10 mm in liquids/colloids with average sensitivity and selectivity. [4]

**Measurement / probe concept:** Fiber-optic transmission or transflection probe immersed in the process line. Uses standard lamps and glass optics. [2, 4]

**Spectral range:** 780-2500 nm [2, 4]

**Limitations:**

- Average selectivity may struggle with complex multicomponent mixtures without advanced chemometrics.
- Signal overlap from water or major components may obscure contaminant signals.

**Assumptions to confirm:**

- The suspected contaminants contain functional groups with NIR-active overtones (e.g., C-H, O-H, N-H).

### Raman Spectroscopy

Raman spectroscopy records weak radiation signals from laser irradiation, providing molecular vibrational information via Stokes shift. [5]

Raman has high selectivity and can analyze liquids, powders, and solids with a penetration depth of 2-5 mm. [6]

**Measurement / probe concept:** Raman fiber probe with laser excitation. Suitable for liquids and solids. [5, 6]

**Spectral range:** Unknown (depends on laser line and detector) [5, 6]

**Limitations:**

- High cost compared to NIR.
- Requires laser excitation, which may pose safety or compatibility issues in some processes.
- Fluorescence from the sample can interfere with Raman signals.

**Assumptions to confirm:**

- The contaminants exhibit distinct Raman shifts that do not overlap significantly with the process matrix.

## Important clarification questions

- What are the physical and chemical properties of the suspected contaminants? — Selection between NIR (general organic) and Raman (specific fingerprint) or other methods.
- What is the acceptable response time for process control? — Choice of spectrometer type (e.g., DAD for speed vs. FTIR for resolution).
- What is the available budget range? — Feasibility of high-cost Raman systems versus average-cost NIR.

## Shared risks and evidence gaps

- Target analyte is unknown, preventing definitive method selection.
- Process compatibility (cleaning, pressure, temperature) for online probes is unverified.

## First feasibility study

Determine if NIR or Raman spectroscopy can detect the suspected contaminants in the actual process matrix with sufficient accuracy to justify online implementation.

1. Collect representative samples of the clean and contaminated process liquid.
2. Perform NIR transmission/reflection and Raman scattering measurements on these samples using fiber-optic probes.
3. Obtain independent reference values for contaminants using standard laboratory methods (e.g., chromatography).
4. Compare spectral predictions against reference values to assess sensitivity, selectivity, and robustness.

## Criteria to agree and check

- NIR or Raman provides a statistically significant correlation with reference values for the suspected contaminants.
- The simpler/lower-cost method (likely NIR) meets the accuracy requirements; if not, Raman is justified.
- No useful improvement is gained from combining methods if one single method suffices.

## Sources

1. S01 PDF p. 7 — Multispectral Laboratory

   The information provided by a particular optical spectral method may be insufficient when analyzing real samples. These include complex multicomponent mixtures, most often of natural origin, such as oil, milk, process me…

2. S01 PDF p. 10 — Multispectral Laboratory

   5.2. Near-IR Spectrometry (includes FTIR, NIR-IR, or diffraction grating/slit spectrometer, Diffuse Reflection fiber probe, or Transmission fiber probe) Near-IR vibrational spectroscopy (NIR) is the most common online an…

3. S01 PDF pp. 4–5 — Multispectral Laboratory

   These differences in methods create natural limitations for their use, meaning that a given practical analysis task requires selecting the most optimal method and approach (technique) for its solution. For example, NIR s…

4. S01 Table 1 (p.6) — AFS Method Comparison Table

   method_no: 2 method: NIR spectroscopy measurement_technique: transmission or transflection probe physical_phenomenon: transmission sample_type: liquid, colloidal optical_path_or_depth: 1-10 mm sensitivity: average select…

5. S01 PDF p. 11 — Multispectral Laboratory

   5.3. Raman Spectroscopy (The kit includes a Raman spectrometer, laser, and Raman fiber probe.) Measurement of the Raman scattering (RS) effect, is also a type of vibrational spectroscopy. Raman spectroscopy is based on r…

6. S01 Table 1 (p.6) — AFS Method Comparison Table

   method_no: 3 method: Raman spectroscopy measurement_technique: Raman probe physical_phenomenon: Raman scattering (emission) sample_type: liquid, powder, solid optical_path_or_depth: 2-5 mm sensitivity: average selectivit…

7. S01 Table 1 (p.6) — AFS Method Comparison Table

   method_no: 4 method: UV/Visible spectroscopy measurement_technique: ATR probe physical_phenomenon: absorption sample_type: liquid optical_path_or_depth: 0.1-1 um sensitivity: low selectivity: low cost: average…

8. S01 PDF p. 5 — Multispectral Laboratory

   It has very high sensitivity, especially in the UV region (190-360 nm), but requires the use of special light sources and fiber optic probes. However, the strong overlap of spectral signals in UV spectra complicates the …

9. S01 PDF p. 4 — Multispectral Laboratory

   3.1. Information Capability of Spectral Methods Spectral analysis provides information about the chemistry and morphology of the sample being studied: the concentrations of its components, their distribution, and particl…
