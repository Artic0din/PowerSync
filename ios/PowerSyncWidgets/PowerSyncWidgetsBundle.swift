//
//  PowerSyncWidgetsBundle.swift
//  PowerSyncWidgets
//

import SwiftUI
import WidgetKit

@main
struct PowerSyncWidgetsBundle: WidgetBundle {
    var body: some Widget {
        BatteryGaugeWidget()
        ImportPriceWidget()
        StatusLockScreenWidget()
        OptimizationLiveActivity()
    }
}
