<!-- release: v2.12.1312 -->

## New

- EPEX users can now opt in to a raw wholesale export forecast. It uses a separate zero-surcharge, zero-tax EPEX Predictor request for the selected bidding zone, including Swedish SE1-SE4 zones.

## Details

- Raw export values are modelled wholesale values in EUR ct/kWh. Import surcharges, network charges, levies, and tax are not applied to export valuation.
- Negative wholesale values remain visible as negative export earnings, but the optimizer clamps their export revenue to zero.
- Fixed export rate remains the default. Custom export price sensors remain available with ct/kWh or EUR/kWh scalar and forecast values.
