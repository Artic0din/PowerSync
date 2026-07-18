//
//  BatteryGaugeWidget.swift
//  PowerSyncWidgets
//

import SwiftUI
import WidgetKit

struct BatteryGaugeProvider: TimelineProvider {
    func placeholder(in context: Context) -> BatteryEntry {
        BatteryEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (BatteryEntry) -> Void) {
        completion(BatteryEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<BatteryEntry>) -> Void) {
        let entry = BatteryEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder)
        completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(300))))
    }
}

struct BatteryEntry: TimelineEntry {
    let date: Date
    let snapshot: SharedEnergySnapshot
}

struct BatteryGaugeWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "BatteryGaugeWidget", provider: BatteryGaugeProvider()) { entry in
            VStack {
                Gauge(value: entry.snapshot.batteryPercent, in: 0...100) {
                    Text("Battery")
                } currentValueLabel: {
                    Text("\(Int(entry.snapshot.batteryPercent))%")
                }
                .gaugeStyle(.accessoryCircularCapacity)
                .tint(.mint)
                Text("Battery")
                    .font(.caption2)
            }
            .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Battery")
        .description("Shows PowerSync battery state of charge.")
        .supportedFamilies([.accessoryCircular, .systemSmall])
    }
}
