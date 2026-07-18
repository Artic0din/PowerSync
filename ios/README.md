# PowerSync for iOS

Native **Swift 6** / **SwiftUI** front end for the PowerSync Home Assistant integration.

- Apple frameworks only: SwiftUI, Charts, WidgetKit, ActivityKit, Observation, Foundation, Security
- No third-party packages
- Human Interface Guidelines patterns: `TabView`, `NavigationStack`, `List` / `Form`, `Gauge`, `Picker`, `Toggle`, SF Symbols, system materials / Liquid Glass (`glassEffect`)
- Deployment target: **iOS 26.0+** (Liquid Glass). Built for the iOS 26/27 design language.

## Open & run

1. On a Mac with **Xcode 26+** (iOS 26/27 SDK)
2. Open `ios/PowerSync.xcodeproj`
3. Select the **PowerSync** scheme and an iPhone simulator or device
4. Set your Development Team under Signing & Capabilities (App Group: `group.cc.powersync.mobile`)
5. Run (⌘R)

Demo Mode works with no Home Assistant. To connect live data:

1. Settings → Connect Home Assistant  
2. Enter your HA URL and a long-lived access token  
3. PowerSync calls native `URLSession` REST endpoints (`/api/config`, `/api/states`, `/api/services/power_sync/...`)

## What’s included

| Area | Implementation |
|------|----------------|
| Dashboard | Live energy glance, prices, optimization summary, force actions |
| Controls | Force charge/discharge, backup reserve, operation mode, grid export, off-grid |
| Automations | Toggleable price/solar/grid rules |
| Settings | Battery health, EV charging, provider/prices, connection, demo mode |
| Smart Optimization | Mode picker, 24h plan (Swift Charts), refresh |
| Prices & Tariff | Forecast chart, TOU strip, tariff window chips |
| EV Charging | Vehicle SOC gauge, smart charging modes |
| Widgets | Battery, import price, status (WidgetKit) |
| Live Activity | Dynamic Island / Lock Screen (ActivityKit) |

## Regenerate the Xcode project

If you add Swift files, update the lists in `scripts/generate_xcodeproj.py` and run:

```bash
python3 scripts/generate_xcodeproj.py
```

## Notes

- This environment cannot compile or run the iOS Simulator (Linux host). Build on macOS/Xcode.
- App icons are placeholder — drop a 1024×1024 image into `PowerSync/Resources/Assets.xcassets/AppIcon.appiconset`.
- Tesla “Sign in” in the product marketing sense maps here to **Connect Home Assistant** (and PowerSync’s HA Tesla OAuth flow); the mobile app talks to HA, not Tesla directly.
