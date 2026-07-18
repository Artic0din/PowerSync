//
//  StatusLockScreenWidget.swift
//  PowerSyncWidgets
//

import SwiftUI
import WidgetKit

struct StatusProvider: TimelineProvider {
    func placeholder(in context: Context) -> StatusEntry {
        StatusEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (StatusEntry) -> Void) {
        completion(StatusEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<StatusEntry>) -> Void) {
        let entry = StatusEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder)
        completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(300))))
    }
}

struct StatusEntry: TimelineEntry {
    let date: Date
    let snapshot: SharedEnergySnapshot
}

struct StatusLockScreenWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "StatusLockScreenWidget", provider: StatusProvider()) { entry in
            VStack(alignment: .leading, spacing: 4) {
                Label("PowerSync", systemImage: "bolt.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.mint)
                Text("Now: \(entry.snapshot.currentAction)")
                    .font(.caption2)
                Text("Next: \(entry.snapshot.nextAction)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("Savings +\(entry.snapshot.todaySavings.formatted(.currency(code: "AUD")))")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.green)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Optimization Status")
        .description("Current and next PowerSync actions.")
        .supportedFamilies([.accessoryRectangular, .systemMedium])
    }
}
