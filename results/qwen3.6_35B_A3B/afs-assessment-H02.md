# AFS technical assessment

Our dairy process handles both a relatively clear whey stream and an opaque
milk concentrate. We want a pilot for composition monitoring, using laboratory
protein and fat results where those variables are relevant. We can compare
different measurement interfaces before choosing the installation. What first
measurement concepts and comparison experiment would make sense for these two
streams?

Evidence release: afs-official-v2

Source citations: present_with_gaps (not scientific verification)

Decision status: Further clarification needed

## Answer

NIR spectroscopy is the established method for inline dairy composition monitoring, with transmission and diffuse reflection probes serving distinct physical constraints [1] [2]. For the clear whey stream, a transmission probe is suitable because it allows light to interact with the entire sample volume, providing higher accuracy for low-concentration components [2]. However, transmission fails in opaque or fat-rich streams where light cannot penetrate [2]. For the opaque milk concentrate, a diffuse reflection probe is required; this technique uses backscattered light from a flush-mounted probe and is sensitive to surface characteristics and fat globule size [1] [3]. The spectral range for NIR typically spans 780–2500 nm, capturing overtones of -OH, -NH2, and -CH bonds relevant to protein and fat [4].

We propose a side-by-side pilot study using representative laboratory samples of both streams. For whey, we will test a transmission probe (optical path 1–10 mm) against lab protein/fat results [5]. For concentrate, we will test a diffuse reflection probe (sampling depth ~0.5–1 mm) against the same reference values [6]. Data processing for reflection data should include scatter correction (MSC or SNV) and PLS regression to account for particle size effects [3]. This approach allows direct comparison of accuracy and robustness before committing to permanent installation.

What are the specific target variables (e.g., fat %, protein %) and acceptable error limits for each stream?

Laboratory findings do not guarantee production performance; installation compatibility, cleaning protocols, and long-term stability remain unverified. [1]

## Full assessment

## Process requirements and constraints

- Our dairy process handles both a relatively clear whey stream and an opaque milk concentrate.
  Customer: "Our dairy process handles both a relatively clear whey stream and an opaque milk concentrate."
- We want a pilot for composition monitoring, using laboratory protein and fat results where those variables are relevant.
  Customer: "We want a pilot for composition monitoring, using laboratory protein and fat results where those variables are relevant."

## Approaches worth testing

### NIR Transmission Probe

Suitable for dilute process streams like whey filtration where light can penetrate the sample volume. [2]

Provides higher accuracy for low-concentration components due to full-volume interaction. [2]

**Measurement / probe concept:** Transmission probe with 1-10 mm optical path, inserted into the whey stream. [2, 5]

**Limitations:**

- Fails in opaque or highly concentrated dairy products where light cannot penetrate the sample.

**Assumptions to confirm:**

- The whey stream remains sufficiently clear and does not become too thick or fat-rich.

### NIR Diffuse Reflection Probe

Used for opaque or highly concentrated dairy products such as yogurt and milk concentrates. [1]

Collects backscattered light from a flush-mounted probe interacting directly with the flowing medium. [3]

**Measurement / probe concept:** Diffuse reflection probe with 0.5-1 mm sampling depth, flush-mounted in the concentrate line. [1, 3, 6]

**Limitations:**

- Sensitive to surface characteristics and fat globule size due to shallow light penetration (0.5-1 mm).

**Assumptions to confirm:**

- Data processing includes scatter correction to mitigate sensitivity to fat globule size.

## Important clarification questions

- What are the specific target variables (e.g., fat %, protein %) and acceptable error limits for each stream? — Selection of probe geometry and validation criteria.

## First feasibility study

Determine if NIR transmission (whey) and diffuse reflection (concentrate) probes meet accuracy requirements for protein and fat monitoring compared to laboratory reference methods.

1. Collect representative samples of whey and milk concentrate across expected process variations.
2. Measure each sample using both a transmission probe (1-10 mm path) and a diffuse reflection probe (flush-mounted).
3. Apply scatter correction (MSC/SNV) and PLS regression to the spectral data. [3]
4. Compare NIR predictions against laboratory protein and fat results for each stream separately.

## Criteria to agree and check

- NIR predictions for whey transmission meet the required error limits for low-concentration components.
- NIR predictions for concentrate reflection are robust to fat globule size variations.
- No single probe type is adequate for both streams, confirming the need for dual-geometry setup.

## Sources

1. S01 PDF p. 17 — Multispectral Laboratory

   4. Inline Milk Analysis. The use of Near-Infrared (NIR) spectroscopy for the inline monitoring of milk is a cornerstone of modern dairy "Process Analytical Technology" (PAT). Maintaining both NIR Reflection and NIR Trans…

2. S01 PDF p. 18 — Multispectral Laboratory

   This technique generally offers higher accuracy for low-concentration components because the light interacts with the entire volume of the sampled cross-section, providing a more representative chemical signature. The Ad…

3. S01 PDF pp. 17–18 — Multispectral Laboratory

   Experimentally, the probe is flush-mounted into a pipe or vat to interact directly with the flowing medium. Because the light does not penetrate deeply, the method is sensitive to the surface characteristics and fat glob…

4. S01 PDF p. 10 — Multispectral Laboratory

   5.2. Near-IR Spectrometry (includes FTIR, NIR-IR, or diffraction grating/slit spectrometer, Diffuse Reflection fiber probe, or Transmission fiber probe) Near-IR vibrational spectroscopy (NIR) is the most common online an…

5. S01 Table 1 (p.6) — AFS Method Comparison Table

   method_no: 2 method: NIR spectroscopy measurement_technique: transmission or transflection probe physical_phenomenon: transmission sample_type: liquid, colloidal optical_path_or_depth: 1-10 mm sensitivity: average select…

6. S01 Table 1 (p.6) — AFS Method Comparison Table

   method_no: 2 method: NIR spectroscopy measurement_technique: diffuse reflection probe physical_phenomenon: diffuse reflection sample_type: scattering media, powder optical_path_or_depth: 0.5-1 mm sensitivity: average sel…
