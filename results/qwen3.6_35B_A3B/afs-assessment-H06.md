# AFS technical assessment

We read the supplied LED NIR kidney study and want to use the same concept to
guide tumor boundary decisions during live surgery. Can we quote its findings
as proof that the proposed sensor is ready for that use? We have no new in
vivo data and no validation on our own setup. What assessment and next
feasibility steps are appropriate?

Evidence release: afs-official-v2

Source citations: present_with_gaps (not scientific verification)

Decision status: Insufficient supporting evidence

## Answer

No. The supplied LED NIR kidney study is an in vitro feasibility proof-of-concept on limited biopsy pairs, not a validation of clinical readiness for live surgery [1] [2]. It explicitly reports high measurement variance in cancer tissue and identifies sensor accuracy, specificity, wavelength optimization, and environmental control as unresolved issues requiring further development [1] [3]. The study's conclusions are confined to demonstrating that the sensor can capture tissue variances at selected wavelengths, not that it is ready for diagnostic decision-making in vivo [2].

The evidence is limited to ex vivo/in vitro samples from a small number of patients, with no in vivo data or validation on your specific setup [2] [4]. The source study itself states that the sensor's general sensitivity provides motivation for further development rather than confirming clinical suitability [1].

To assess feasibility for live surgery, you must establish whether the LED-based approach can meet your diagnostic requirements on representative tissue under surgical conditions. A practical first study would involve screening the proposed sensor concept against a reference method (e.g., standard spectroscopy or histopathology) on fresh ex vivo tissue samples that mimic the surgical environment (temperature, lighting, probe contact). The study should test the LED sensor's ability to distinguish tumor from normal tissue with acceptable variance and specificity, as identified as the main limitation in the source [1]. [5]

What are your specific diagnostic requirements for tumor boundary detection during live surgery (e.g., acceptable false negative rate, response time, and tissue type variability)?

## Full assessment

## Process requirements and constraints

- Use the same concept to guide tumor boundary decisions during live surgery.
  Customer: "We read the supplied LED NIR kidney study and want to use the same concept to guide tumor boundary decisions during live surgery."
- No new in vivo data and no validation on our own setup.
  Customer: "We have no new in vivo data and no validation on our own setup."

## Approaches worth testing

### LED-based NIR Sensor (as per source study)

The source study demonstrates that an LED-based NIR sensor can capture cancer-related tissue variances at selected wavelengths in the 900-1500 nm range on in vitro kidney samples. [2, 5]

The study reports high measurement variance in cancer tissue and identifies the need for improved accuracy and specificity, noting that false negatives are unacceptable outcomes. [1]

**Measurement / probe concept:** Diffuse reflectance NIR sensing using a fiber optic probe with specific LED wavelengths (example: 900-1500 nm range) to capture tissue optical properties. Installation and cleaning compatibility with surgical instruments must be verified separately. [1, 2, 4, 6, 5]

**Limitations:**

- High measurement variance in cancer tissue was a primary disadvantage in the source study. [1]
- Limited sample size and ex vivo/in vitro nature of the validation data. [2, 4]
- Requires individual adaptation of LED set and wavelengths for each case/tissue type. [6]

**Assumptions to confirm:**

- The LED wavelengths can be optimized to capture relevant biomarkers in the specific tumor types encountered in live surgery.
- Probe contact and environmental conditions in the surgical setting can be controlled to minimize the variance observed in the source study.

## Important clarification questions

- What are your specific diagnostic requirements for tumor boundary detection during live surgery (e.g., acceptable false negative rate, response time, and tissue type variability)? — Selection of sensor technology and validation criteria.

## Shared risks and evidence gaps

- The source study's sample size was limited by ethical and availability constraints. [4]
- Optimization of LED wavelengths, measurement conditions, and data processing remains an open area for future work. [3]

## First feasibility study

Determine if the LED-based NIR sensor concept can meet diagnostic accuracy and robustness requirements for tumor boundary detection in live surgery, compared to a reference method.

1. Collect fresh ex vivo tissue samples representing the target surgical scenarios (tumor types, normal tissue, varying optical properties).
2. Perform simultaneous measurements with the proposed LED sensor and a high-fidelity reference method (e.g., full-spectrum NIR or histopathology) on the same tissue regions.
3. Evaluate sensor performance (sensitivity, specificity, variance) against the reference results across the sample set.
4. Assess robustness to surgical variables (probe contact pressure, ambient light, temperature) if applicable.

## Criteria to agree and check

- The LED sensor meets pre-defined diagnostic accuracy and precision requirements for tumor boundary detection.
- The sensor's performance is robust enough to justify its use over simpler or alternative methods in the surgical environment.
- No significant bias or variance issues prevent reliable decision-making.

## Sources

1. S03 PDF p. 14 — Development and Testing of an LED-Based Near-Infrared Sensor

   The main disadvantage of the LED-based sensor in its present form is a very high measurement variance in cancer tissue samples (Figure 9). The main requirement for the sensor improvement is therefore to increase the accu…

2. S03 PDF p. 2 — Development and Testing of an LED-Based Near-Infrared Sensor

   To the best of our knowledge, the use of LED-based optical sensing for kidney cancer diagnostics has not yet been reported. Two sensor prototypes were implemented and tested for kidney cancer diagnostics using seven pair…

3. S03 PDF pp. 14–15 — Development and Testing of an LED-Based Near-Infrared Sensor

   The directions for the further improvement of the sensor are (1) LED number and wavelength optimization, (2) optimization of the measurement conditions (distance, angle, light intensity, spectral reference, probe constru…

4. S03 PDF p. 6 — Development and Testing of an LED-Based Near-Infrared Sensor

   2.5. Model Validation Considering the limited availability of the biopsy material and the ethical aspects of its use, conclusions about the general suitability of the NIR sensor analysis for cancer diagnostics and, in pa…

5. S03 PDF p. 2 — Development and Testing of an LED-Based Near-Infrared Sensor

   Inexpensive sensors may perform as well as the respective full-scale spectroscopic techniques, or even better [20]. An inevitable loss of accuracy as a result of a much lower sensor resolution is compensated for by exclu…

6. S03 PDF p. 14 — Development and Testing of an LED-Based Near-Infrared Sensor

   For example, examinations of various organs, the selection of optimal wavelengths, and data processing methods must be addressed. The presented technology of diffuse reflectance NIR sensing for tumor diagnostics can be e…
