//
//  SharedSnapshotWriter.swift
//  PowerSync
//
//  Writes a compact snapshot for WidgetKit / Live Activities via App Group.
//

import Foundation
import WidgetKit

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
}

enum SharedSnapshotWriter {
    static let suiteName = "group.cc.powersync.mobile"
    static let snapshotKey = "sharedEnergySnapshot"

    @MainActor
    static func write(from model: AppModel) {
        let snapshot = SharedEnergySnapshot(
            batteryPercent: model.snapshot.batteryPercent,
            importCents: model.snapshot.importCents,
            solarKW: model.snapshot.solarKW,
            currentAction: model.optimization.currentAction,
            nextAction: model.optimization.nextAction,
            nextTime: model.optimization.nextTime,
            todaySavings: model.optimization.todaySavings,
            exportKW: max(0, -model.snapshot.gridWatts) / 1000.0,
            updatedAt: .now
        )
        guard let defaults = UserDefaults(suiteName: suiteName),
              let data = try? JSONEncoder().encode(snapshot)
        else { return }
        defaults.set(data, forKey: snapshotKey)
        WidgetCenter.shared.reloadAllTimelines()
        LiveActivityManager.sync(from: model)
    }

    static func read() -> SharedEnergySnapshot? {
        guard let defaults = UserDefaults(suiteName: suiteName),
              let data = defaults.data(forKey: snapshotKey),
              let snapshot = try? JSONDecoder().decode(SharedEnergySnapshot.self, from: data)
        else { return nil }
        return snapshot
    }
}
