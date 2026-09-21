# Sparse connectome controllers under a sparse delayed liquid-biopsy-style observer

**Thesis #27.** Computational research, set out in Nile University B.Sc. chapter order for handoff.

**Depends on:** Thesis #6 (sparse connectome controllers) and Thesis #20 (occult modes under a sparse delayed liquid-biopsy-style partial observer).

**Author:** Kelechi Emeka Ogbonna  
**Email:** kelechiogbonna300@gmail.com  
**GitHub:** https://github.com/cloudynirvana  
**Date:** 21 September 2026

Can sparse connectome-style controllers designed under full or dense observation still meet their declared steering bounds when the only map is the sparse delayed liquid-biopsy-style partial observer of Thesis #20?

They cannot. The shared object is one frozen sparse controller on the two-clone plant of Thesis #6, scored against inequalities fixed before the partial-observer paths were read. Full state meets the certificate. A dense zero-order hold of both clones, on the two-unit clock of Thesis #20, also meets it, in the noise-free paths and in all 80 noisy replicates. The sparse delayed scalar of Thesis #20, lag 14, clock 20, noise standard deviation 0.03, floor 0.10, with the unread fraction imputed at 1/2, fails separation (gap 0), action tracking (mean absolute deviation 0.377434), duty (0.680515, 0.707532, and 0.673022, against a band ending at 0.30), and input deviation (0.579898 to 0.656736, against a cut of 0.20). Mean burden, terminal burden, peak burden, and state deviation still pass. The closest state call is 0.145728 against 0.15. On that same scalar the sensitive coordinate ends at 0, which the design loop does not do. Restoring the true fraction at the sparse instants returns the noise-free certificate. The floor never censors these paths.

No number is taken from either deposit's results file. Thesis #6's isoline correlations are not results of this toy. Thesis #20's mode-error rates are not results of this toy. This deposit does not claim a dose or a clinical controller.

This is research only. It is not a medical device, not clinical decision support, not a dose, and not a cure. No document DOI is registered.

See [DISCLAIMER.md](DISCLAIMER.md). The manuscript is [THESIS.md](THESIS.md).

## Files

| Path | Role |
| --- | --- |
| `THESIS.md` | Manuscript (Chapters 1 to 5, Vancouver citations) |
| `THESIS.pdf` | PDF built from the Markdown |
| `build_pdf.py` | Regenerates `THESIS.pdf` |
| `CITATION.cff` | Citation metadata, no document DOI |
| `DISCLAIMER.md` | Research-only boundary |
| `sim/joint_toy.py` | Shared loop: one sparse controller, two online maps (seed 20260921) |
| `sim/results.json` | Numbers cited in Chapter Four |
| `sim/figures/` | Closed loop, isoline, bound calls, Monte Carlo fractions |

## Reproduce

```bash
python3 -m pip install -r sim/requirements.txt
python3 sim/joint_toy.py
python3 build_pdf.py
```

NumPy and Matplotlib are required for the toy. The PDF step also needs the `markdown` and `weasyprint` packages. Regenerating the script rewrites `sim/results.json` and `sim/figures/`.

## Cite

Ogbonna KE. Sparse connectome controllers under a sparse delayed liquid-biopsy-style observer [Internet]. Thesis #27 computational research thesis. 21 September 2026 [cited YYYY Mon DD]. Available from: https://github.com/cloudynirvana/thesis-27-sparse-controllers-partial-lb-observer

Machine-readable fields are in `CITATION.cff`. Add a document DOI there only after one exists.

Hub index, for cataloguing only: [research-theses-hub](https://github.com/cloudynirvana/research-theses-hub).

## Licence

Text and sketch code are MIT, with attribution. Computational research only.
