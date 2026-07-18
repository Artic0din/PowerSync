//
//  ImportPriceWidget.swift
//  PowerSyncWidgets
//

import SwiftUI
import WidgetKit

struct ImportPriceProvider: TimelineProvider {
    func placeholder(in context: Context) -> PriceEntry {
        PriceEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (PriceEntry) -> Void) {
        completion(PriceEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<PriceEntry>) -> Void) {
        let entry = PriceEntry(date: .now, snapshot: SharedEnergySnapshot.read() ?? .placeholder)
        completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(300))))
    }
}

struct PriceEntry: TimelineEntry {
    let date: Date
    let snapshot: SharedEnergySnapshot
}

struct ImportPriceWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ImportPriceWidget", provider: ImportPriceProvider()) { entry in
            VStack(alignment: .leading, spacing: 4) {
                Text("Import")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("\(entry.snapshot.importCents.formatted(.number.precision(.fractionLength(0))))¢")
                    .font(.title.weight(.bold))
                    .fontDesign(.rounded)
                    .foregroundStyle(.red)
                Text("Solar \(entry.snapshot.solarKW.formatted(.number.precision(.fractionLength(1)))) kW")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Import Price")
        .description("Live import price and solar generation.")
        .supportedFamilies([.accessoryRectangular, .systemSmall])
    }
}
