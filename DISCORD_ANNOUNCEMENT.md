# Discord Announcement - Flow Power + AEMO Support

---

## New Feature: Flow Power + AEMO Wholesale Pricing Support

Hey everyone! Big update today for **Flow Power** customers (and anyone else on wholesale electricity plans).

### What's New

**Full AEMO Wholesale Price Support**
Tesla Sync now fully supports using AEMO NEM wholesale prices as your primary price source. This is perfect for:
- Flow Power customers
- Other wholesale/spot price retailers
- Anyone who wants to use raw AEMO data instead of Amber

**Network Tariff Configuration**
Since AEMO only provides wholesale prices (no network fees), you can now configure your DNSP (Distribution Network) charges directly in Settings:

- **Flat Rate** - Single rate all day
- **Time of Use** - Peak/Shoulder/Off-Peak with configurable time windows
- **Other Fees** - Environmental levies, market charges (~3-4c/kWh typical)
- **GST Toggle** - Automatically adds 10%

Your total price = AEMO Wholesale + Network Charges + Other Fees + GST

**What's Included**
- Live current price display with network charges applied
- TOU schedule showing accurate total prices (not just wholesale)
- Price history tracking for AEMO users
- Auto-detection of dollars vs cents (if you accidentally enter $0.19 instead of 19c, we'll fix it!)
- Flow Power Happy Hour export rates (45c NSW/QLD/SA, 35c VIC) from 5:30pm-7:30pm

### How to Set Up

1. Go to **Settings**
2. Select **Flow Power** as your electricity provider
3. Choose your **NEM Region** (QLD1, NSW1, VIC1, SA1)
4. Select **AEMO NEM Wholesale** as price source
5. Configure your **Network Tariff**:
   - Find your DNSP rates (Energex, Ausgrid, etc.)
   - Enter rates in **cents/kWh** (e.g., if tariff shows $0.19367/kWh, enter `19.367`)
   - Set your peak/off-peak time windows
   - Add other fees (~3-4c typical)
   - Enable GST

### Example: Energex NTC6900 Tariff

| Period | Rate |
|--------|------|
| Peak (4pm-9pm) | 19.37 c/kWh |
| Shoulder | 4.87 c/kWh |
| Off-Peak (11am-4pm) | 0.48 c/kWh |
| Other Fees | 3-4 c/kWh |

### Breaking Down Amber vs AEMO

If you're wondering what goes into Amber's per-kWh price:
- Wholesale (AEMO spot)
- Network usage charges
- Carbon offset (~0.3c)
- Environmental certificates (~0.9c)
- Market charges (~0.9c)
- Price protection (~1c)

With AEMO mode, you're getting the raw wholesale price and adding your actual network charges - potentially more accurate for non-Amber retailers!

---

**Update now:** `docker pull bolagnaise/tesla-sync:latest`

Questions? Drop them below!
