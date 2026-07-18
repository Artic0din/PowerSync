//
//  OptimizationLiveActivity.swift
//  PowerSyncWidgets
//

import ActivityKit
import SwiftUI
import WidgetKit

struct OptimizationActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable, Sendable {
        var currentAction: String
        var nextAction: String
        var exportKW: Double
        var todaySavings: Double
    }

    var siteName: String
}

struct OptimizationLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: OptimizationActivityAttributes.self) { context in
            HStack {
                Label(context.state.currentAction, systemImage: "bolt.fill")
                    .font(.headline)
                Spacer()
                Text("+\(context.state.todaySavings.formatted(.currency(code: "AUD")))")
                    .font(.headline)
                    .fontDesign(.rounded)
                    .foregroundStyle(.green)
            }
            .padding()
            .activityBackgroundTint(.black.opacity(0.35))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("PowerSync", systemImage: "bolt.fill")
                        .font(.caption)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("+\(context.state.todaySavings.formatted(.currency(code: "AUD")))")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.green)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack {
                        Text(context.state.currentAction)
                        Spacer()
                        Text("Next: \(context.state.nextAction)")
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                }
            } compactLeading: {
                Image(systemName: "bolt.fill")
                    .foregroundStyle(.mint)
            } compactTrailing: {
                Text("\(context.state.exportKW.formatted(.number.precision(.fractionLength(1)))) kW")
                    .font(.caption2)
            } minimal: {
                Image(systemName: "bolt.fill")
            }
        }
    }
}
