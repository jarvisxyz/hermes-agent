#!/usr/bin/env python3
"""
Ontario Vehicle Depreciation Analysis - 2025/2026
Based on real market data gathered from:
- VW dealership pricing (Milton VW, VW of Newmarket)
- Toyota media releases and dealer sites
- Kelley Blue Book depreciation data
- VMR Canada used car values
- Motor Illustrated, Driving.ca, EmptyTank automotive journalism
"""

import json

# ============================================================
# ONTARIO TAX / REBATE STRUCTURE
# ============================================================
HST = 0.13  # 13% Ontario HST

# iZEV (federal plug-in EV incentive)
# As of 2025-2026, the iZEV program offers:
# - $5,000 for vehicles with MSRP under $55,000
# - $2,500 for vehicles with MSRP between $55,000-$65,000
# Note: As of late 2024/2025, the government announced the iZEV program
# funding is winding down, but for analysis we assume it's still available
# RAV4 Prime PHEP qualifies as a long-range PHEP
IZEV_REBATE_PHEP = 2500  # PHEPs get $2,500 (lower amount since 2024 changes)
# Note: The 2026 RAV4 Plug-in Hybrid starts at $48,750 which is under $55k
# but PHEPs receive $2,500 not $5,000

# Ontario used car PST rule:
# Ontario eliminated the separate PST on used cars in 2010 - only HST (13%) 
# applies to used cars from private sales if you pay fair market value
# Dealerships charge HST on used car sales
# For private sales, buyer pays HST on the USED CAR VALUE (not purchase price)
# if below book value - but commonly dealers and private sales both = 13% HST

# ============================================================
# VEHICLE 1: Used 2022 VW Tiguan (80k km, listed $25,000)
# ============================================================
# 
# DATA SOURCES:
# - KBB: 2022 Tiguan depreciated 38% over 3 years, avg resale ~$15,100
# - VMR Canada: 2022 Tiguan Highline retail ~$27,825, projected: 
#   1yr: $24,800, 3yr: $16,325, 5yr: $11,000
# - Kijiji listing: $30,907 OBO with 48,837 km (higher trim, lower km)
# - CarWise Canada: $24,799 with 102,885 km
# - CarGurus: "Save $5,950 on 2022 Tiguan near you" (average deal)
#
# Given: 2022 Tiguan, 80k km, listed at $25,000
# This is reasonable market price for the condition/mileage
# Since already 3-4 years old, depreciation curve is SHALLLOWER

TIGUAN_2022 = {
    "vehicle": "Used 2022 VW Tiguan (80,000 km)",
    "current_listed_price": 25000,  # as-stated listing price
    "description": "Already depreciated significantly; used car shallower curve",
    
    # Depreciation rates for USED 3-4 year old VW Tiguan
    # Based on KBB data: ~$2,500-3,500/year for used Tiguans
    # VMR Canada curve for Highline: $27,825 -> $16,325 (3yr) -> $11,000 (5yr)
    # That's about 12-15% annual depreciation on remaining value for used
    
    "depreciation": {
        # From today (vehicle is ~4 years old, 80k km)
        # Year 3 from now: vehicle will be ~7 years old, ~130k-145k km
        # Year 5 from now: vehicle will be ~9 years old, ~180k km
        # Year 7 from now: vehicle will be ~11 years old, ~230k km
        # Year 10 from now: vehicle will be ~14 years old, ~300k km
        
        "year_3": {
            "year": 2029,
            "vehicle_age": 7,
            "est_km": 140000,
            "depreciated_value": 15500,
            "pct_of_current": 0.62,
            "note": "~38% depreciation over 3 more years from used starting point"
        },
        "year_5": {
            "year": 2031,
            "vehicle_age": 9,
            "est_km": 180000,
            "depreciated_value": 11800,
            "pct_of_current": 0.47,
            "note": "~53% depreciation over 5 years from used point"
        },
        "year_7": {
            "year": 2033,
            "vehicle_age": 11,
            "est_km": 230000,
            "depreciated_value": 8200,
            "pct_of_current": 0.33,
            "note": "~67% depreciation - high mileage affects value"
        },
        "year_10": {
            "year": 2036,
            "vehicle_age": 14,
            "est_km": 300000,
            "depreciated_value": 4500,
            "pct_of_current": 0.18,
            "note": "Floor value approaching scrap/residual"
        }
    }
}

# ============================================================
# VEHICLE 2: New 2026 VW Tiguan Highline Turbo R-Line
# ============================================================
# 
# DATA from VW of Newmarket (real dealer site):
# MSRP: $48,995
# Freight & PDI: $2,200
# OMVIC Fee: $22
# Admin Fee: $500 (typical dealer)
# Total before tax: ~$51,717
# 
# KBB 2022 Tiguan depreciation data shows:
# 3-year depreciation: ~38% from new
# New car depreciation year 1: ~20-25% off MSRP
# Year 2-3: another 10-12% each
# Year 5: typically retains ~40-45% of original
#
# VW brand depreciates faster than Toyota - typical compact SUV pattern

TIGUAN_2026_NEW = {
    "vehicle": "New 2026 VW Tiguan Highline Turbo R-Line 4MOTION",
    "msrp": 48995,
    "freight_pdi": 2200,
    "omvic_fee": 22,
    "admin_fee": 500,
    "total_before_tax": 51717,
    "total_with_hst": round(51717 * 1.13, 2),
    "description": "New car, steeper initial depreciation",
    
    "depreciation": {
        # VW Tiguan new car depreciation (based on KBB historical data)
        # Year 1: ~22-25% off new
        # Year 2: ~10-12% additional
        # Year 3: ~8-10% additional (= ~38% total from KBB data)
        # Year 5: ~55% total remaining value
        # Year 7: ~38-42% remaining
        # Year 10: ~22-25% remaining
        
        "year_3": {
            "year": 2029,
            "vehicle_age": 3,
            "est_km": 60000,
            "remaining_pct": 0.62,
            "depreciated_value": round(51717 * 0.62),
            "note": "~38% lost in first 3 years (matches KBB data)"
        },
        "year_5": {
            "year": 2031,
            "vehicle_age": 5,
            "est_km": 100000,
            "remaining_pct": 0.47,
            "depreciated_value": round(51717 * 0.47),
            "note": "53% lost by year 5 - VW depreciation curve"
        },
        "year_7": {
            "year": 2033,
            "vehicle_age": 7,
            "est_km": 140000,
            "remaining_pct": 0.36,
            "depreciated_value": round(51717 * 0.36),
            "note": "64% lost - entering high-mileage range"
        },
        "year_10": {
            "year": 2036,
            "vehicle_age": 10,
            "est_km": 200000,
            "remaining_pct": 0.23,
            "depreciated_value": round(51717 * 0.23),
            "note": "77% lost - significant age and mileage"
        }
    }
}

# ============================================================
# VEHICLE 3: New 2026 Toyota RAV4 Prime XSE
# ============================================================
#
# DATA SOURCES:
# - ErinPark Toyota (March 31, 2026): "2026 Toyota RAV4 Plug-in Hybrid: Four Grades 
#   Now Available Starting at $48,750"
# - 2026 RAV4 Hybrid XSE (non-PHEP): MSRP $50,900; XSE Tech: $52,450
# - The PHEP XSE will be priced HIGHER than the hybrid XSE
# - 2024 (old gen) RAV4 Prime XSE MSRP was ~$49,450 + $1,800 freight = ~$51,250
# - New generation PHEP: Likely $53,000-$55,000 for XSE trim
#   (PHEP starts at $48,750 for base, XSE would be $3,000-$4,000 higher)
#   Estimated: XSE MSRP ~$52,500-$53,500
#   Freight & PDI: ~$1,800-1,950 (Toyota's typical PDI)
#
# iZEV REBATE: $2,500 for PHEPs (federal)
# Ontario has NO provincial EV rebate (OZIP ended in 2018)
# 
# KBB 2024 RAV4 Prime data:
# - 28% depreciation over 3 years
# - Retains 72% of value after 3 years
# - In top 10-25% for depreciation among SUVs
# - 2024 RAV4 Prime: new ~$45,085 -> current resale $32,400
#
# RAV4 Prime has EXCEPTIONAL resale value due to limited supply and high demand

RAV4_PRIME_2026 = {
    "vehicle": "New 2026 Toyota RAV4 Plug-in Hybrid XSE",
    "estimated_xse_msrp": 52500,  # estimated based on hybrid XSE ($50,900) + PHEP premium
    "freight_pdi": 1895,  # Toyota typical PDI
    "total_before_tax": 54395,
    "total_with_hst": round(54395 * 1.13, 2),
    "izev_rebate": 2500,
    "total_with_hst_minus_rebate": round(54395 * 1.13 - 2500, 2),
    "description": "Best-in-class resale value; PHEP demand exceeds supply",
    
    "depreciation": {
        # RAV4 Prime depreciation - EXCEPTIONAL retention
        # KBB 2024 data: 28% over 3 years (72% retained)
        # This was an older model with very high dealer markups
        # New model with better supply but still high demand:
        #
        # Year 1: ~12-15% (Toyota depreciation, but Prime holds well)
        # Year 3: ~28% (KBB data for Prime)
        # Year 5: ~38-42% lost
        # Year 7: ~50-55% lost
        # Year 10: ~62-65% lost
        
        "year_3": {
            "year": 2029,
            "vehicle_age": 3,
            "est_km": 60000,
            "remaining_pct": 0.72,
            "depreciated_value": round(54395 * 0.72),
            "note": "Only 28% lost - best-in-class retention (KBB data)"
        },
        "year_5": {
            "year": 2031,
            "vehicle_age": 5,
            "est_km": 100000,
            "remaining_pct": 0.60,
            "depreciated_value": round(54395 * 0.60),
            "note": "40% lost - still above average"
        },
        "year_7": {
            "year": 2033,
            "vehicle_age": 7,
            "est_km": 140000,
            "remaining_pct": 0.48,
            "depreciated_value": round(54395 * 0.48),
            "note": "52% lost - Toyota reputation sustains value"
        },
        "year_10": {
            "year": 2036,
            "vehicle_age": 10,
            "est_km": 200000,
            "remaining_pct": 0.36,
            "depreciated_value": round(54395 * 0.36),
            "note": "64% lost - still good for a 10-year-old SUV"
        }
    }
}

# ============================================================
# SUMMARY TABLE
# ============================================================

print("=" * 80)
print("ONTARIO VEHICLE DEPRECIATION ANALYSIS - 2025/2026")
print("=" * 80)
print()

# Vehicle 1
print("VEHICLE 1: Used 2022 VW Tiguan (80,000 km) - Listed $25,000")
print("-" * 80)
v1 = TIGUAN_2022
base1 = v1["current_listed_price"]
print(f"  Current listed price:                ${base1:>10,}")
print(f"  + HST (13%):                         ${base1 * HST:>10,.2f}")
print(f"  TOTAL ON-THE-ROAD (estimated):       ${base1 * (1+HST):>10,.2f}")
print()
for key, val in v1["depreciation"].items():
    print(f"  {val['year']} ({key.replace('_',' ')}):  ${val['depreciated_value']:>10,}  ({val['pct_of_current']*100:.0f}% of current value)  |  ~{val['est_km']:,} km")
print()

# Vehicle 2
print("VEHICLE 2: New 2026 VW Tiguan Highline Turbo R-Line")
print("-" * 80)
v2 = TIGUAN_2026_NEW
print(f"  MSRP:                                ${v2['msrp']:>10,}")
print(f"  Freight & PDI:                       ${v2['freight_pdi']:>10,}")
print(f"  OMVIC Fee:                           ${v2['omvic_fee']:>10,}")
print(f"  Admin Fee (avg):                     ${v2['admin_fee']:>10,}")
print(f"  ─────────────────────────────────────────────────")
print(f"  SUBTOTAL (before tax):               ${v2['total_before_tax']:>10,}")
print(f"  + HST (13%):                         ${v2['total_with_hst'] - v2['total_before_tax']:>10,.2f}")
print(f"  TOTAL ON-THE-ROAD:                   ${v2['total_with_hst']:>10,.2f}")
print()
print("  RESIDUAL VALUES (from pre-tax subtotal):")
for key, val in v2["depreciation"].items():
    print(f"  {val['year']} ({key.replace('_',' ')}):  ${val['depreciated_value']:>10,}  ({val['remaining_pct']*100:.0f}% of original)  |  ~{val['est_km']:,} km")
print()

# Vehicle 3
print("VEHICLE 3: New 2026 Toyota RAV4 Plug-in Hybrid XSE")
print("-" * 80)
v3 = RAV4_PRIME_2026
print(f"  Estimated XSE MSRP (est.):           ${v3['estimated_xse_msrp']:>10,}")
print(f"  Freight & PDI:                       ${v3['freight_pdi']:>10,}")
print(f"  ─────────────────────────────────────────────────")
print(f"  SUBTOTAL (before tax):               ${v3['total_before_tax']:>10,}")
print(f"  + HST (13%):                         ${v3['total_with_hst'] - v3['total_before_tax']:>10,.2f}")
print(f"  TOTAL ON-THE-ROAD (before rebate):   ${v3['total_with_hst']:>10,.2f}")
print(f"  - iZEV Federal Rebate:               -${v3['izev_rebate']:>9,}")
print(f"  NET ON-THE-ROAD (after rebate):      ${v3['total_with_hst_minus_rebate']:>10,.2f}")
print()
print("  RESIDUAL VALUES (from pre-tax subtotal):")
for key, val in v3["depreciation"].items():
    print(f"  {val['year']} ({key.replace('_',' ')}):  ${val['depreciated_value']:>10,}  ({val['remaining_pct']*100:.0f}% of original)  |  ~{val['est_km']:,} km")
print()

# ============================================================
# COMPARISON TABLE
# ============================================================
print("=" * 80)
print("COMPARISON SUMMARY TABLE")
print("=" * 80)
print()
print(f"{'':>38} {'VW 2022 Used':>15} {'VW 2026 New':>15} {'RAV4 PHEP':>15}")
print(f"{'':>38} {'(80k km)':>15} {'Highline':>15} {'XSE':>15}")
print("-" * 80)

headers = ["", "25k used Tig", "51.7k new Tg", "54.4k RAV4 PE"]
print(f"{'Purchase price (pre-tax)':>38} ${base1:>13,} ${v2['total_before_tax']:>13,} ${v3['total_before_tax']:>13,}")

# Year 3
print()
print(f"  Year 3 (2029) Residual:", end="")
for v, name in [(TIGUAN_2022, 'T1'), (TIGUAN_2026_NEW, 'T2'), (RAV4_PRIME_2026, 'T3')]:
    val = v['depreciation']['year_3']['depreciated_value']
    print(f" ${val:>13,}", end="")
print()

# Year 5
print(f"  Year 5 (2031) Residual:", end="")
for v in [TIGUAN_2022, TIGUAN_2026_NEW, RAV4_PRIME_2026]:
    val = v['depreciation']['year_5']['depreciated_value']
    print(f" ${val:>13,}", end="")
print()

# Year 7
print(f"  Year 7 (2033) Residual:", end="")
for v in [TIGUAN_2022, TIGUAN_2026_NEW, RAV4_PRIME_2026]:
    val = v['depreciation']['year_7']['depreciated_value']
    print(f" ${val:>13,}", end="")
print()

# Year 10
print(f"  Year 10 (2036) Residual:", end="")
for v in [TIGUAN_2022, TIGUAN_2026_NEW, RAV4_PRIME_2026]:
    val = v['depreciation']['year_10']['depreciated_value']
    print(f" ${val:>13,}", end="")
print()

print()
print("=" * 80)
print("NOTES AND ASSUMPTIONS")
print("=" * 80)
print("""
1. TAXES: Ontario HST is 13% on new and used car sales (dealerships).
   Private-party used sales: Buyer pays HST on the higher of purchase price
   or wholesale value - effectively ~13% in most cases.

2. iZEV REBATE: Federal rebate of $2,500 for PHEPs under $55,000 MSRP.
   The 2026 RAV4 Plug-in Hybrid XSE is estimated at ~$52,500 + $1,895 freight.
   Ontario has no provincial EV rebate (OZIP ended 2018).

3. VW TIGUAN DEPRECIATION: Based on Kelley Blue Book data showing 2022 Tiguan
   depreciated 38% over 3 years. New Tiguans lose ~22% in year 1 alone.
   German luxury/premium brands depreciate faster than Japanese brands.

4. RAV4 PRIME DEPRECIATION: Based on KBB 2024 RAV4 Prime data showing only
   28% depreciation over 3 years (72% retained). Exceptional for the segment.
   Toyota's reputation for reliability + PHEP demand + limited supply = 
   best-in-class resale value.

5. KM ASSUMPTIONS: 18,000-20,000 km/year average driving for new vehicles.
   Used Tiguan: +12,000-15,000 km/year (higher mileage used car pattern).

6. RAV4 PRIME MSRP NOTE: The 2026 RAV4 Plug-in Hybrid starting price of 
   $48,750 (announced March 31, 2026) is for the base grade. The XSE trim 
   is estimated at ~$52,500 based on the $3,000-$4,000 premium over base
   that the 2026 Hybrid XSE carries over the Hybrid LE.
""")

# Save to JSON
with open('/Users/jarvis/.hermes/hermes-agent/work/depreciation_data.json', 'w') as f:
    json.dump({
        "tiguan_2022_used": TIGUAN_2022,
        "tiguan_2026_new": TIGUAN_2026_NEW,
        "rav4_prime_2026": RAV4_PRIME_2026
    }, f, indent=2)

print("Data saved to depreciation_data.json")
