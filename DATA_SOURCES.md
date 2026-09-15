# Reference data sources

The project is designed around versioned Philippine Statistics Authority classifications.

## PSIC Revision 5

Official detailed-structure workbook used by `placetype taxonomy fetch psic`:

https://psa.gov.ph/sites/default/files/scd/PSIC_Revision_5_Detailed_Structure_30July2026.xlsx

Official Board Resolution annex with the Revision 5 detailed structure and explanatory notes:

https://psa.gov.ph/system/files/psa-board/Annex-BR-09-20260521-01.pdf

Classification browser:

https://psa.gov.ph/classification/psic

The browser currently lags the Revision 5 annex in some section labels. Import validation uses the
official Revision 5 source structure, not the stale browser rendering.

## PCPC 2002

Classification browser:

https://psa.gov.ph/classification/pcpc

API documentation:

https://psa.gov.ph/classifications-api/pcpc

## PSCC 2022

Official workbook used by `placetype taxonomy fetch pscc`:

https://psa.gov.ph/system/files?file=scd/2022%20PSCC_11292023.xlsx

Classification browser:

https://psa.gov.ph/classification/pscc

API documentation:

https://psa.gov.ph/classifications-api/pscc

## Licensing

PSA website pages state that website data/content are CC BY 4.0 unless otherwise stated. Check
any file-specific notice before redistributing a downloaded workbook or a normalized derivative.
The code in this repository is separately licensed under MIT.

## Published completeness checks used by PlaceType PH

For **PSIC Revision 5**, PlaceType PH checks the published 22 / 88 / 260 / 493 / 1,338
section-to-subclass structure after normalization.

For **PCPC 2002**, exact counts are not frozen in v0.1.1 because current PSA pages disagree on
the division/item totals. Diagnose the authenticated live API first and then freeze the observed
structure.

For **PSCC 2022**, the five hierarchy levels (chapter, heading, HS subheading, AHTN subheading,
commodity) are known, but exact category counts are not frozen in v0.1.1. Run
`placetype taxonomy diagnose pscc` against the current official workbook first; after a successful
live import, freeze the observed counts and a small source-structure fixture rather than guessing.
