//
//  SharedSnapshot.swift
//  PowerSyncWidgets
//
//  Mirrors SharedEnergySnapshot for the widget extension target.
//

import Foundation

struct SharedEnergySnapshot: Codable, Sendable {
    var batteryPercent: Double
    var importCents: Double
    var solarKW: Double
    var currentAction: String
    var nextAction: String
    var nextTime: Date
    var todaySavings: Double
    var exportKW: Double
    var updatedAt: Date

    static let suiteName = "group.cc.powersync.mobile"
    static let snapshotKey = "sharedEnergySnapshot"

    static func read() -> SharedEnergySnapshot? {
        guard let defaults = UserDefaults(suiteName: suiteName),
              let data = defaults.data(forKey: snapshotKey),
              let snapshot = try? JSONDecoder().decode(SharedEnergySnapshot.self, from: data)
        else { return nil }
        return snapshot
    }

    static var placeholder: SharedEnergySnapshot {
        SharedEnergySnapshot(
            batteryPercent: 45,
            importCents: 31,
            solarKW: 2.1,
            currentAction: "Self-Consumption",
            nextAction: "Charging",
            nextTime: .now.addingTimeInterval(3600),
            todaySavings: 7.31,
            exportKW: 2.3,
            updatedAt: .now
        )
    }
}
