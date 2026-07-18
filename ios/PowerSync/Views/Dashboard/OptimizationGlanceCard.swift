//
//  OptimizationGlanceCard.swift
//  PowerSync
//

import SwiftUI

struct OptimizationGlanceCard: View {
    let summary: OptimizationSummary

    var body: some View {
        NavigationLink {
            OptimizationView()
        } label: {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Smart Optimization")
                        .font(.headline)
                    Spacer()
                    Text(summary.status.rawValue)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .foregroundStyle(.orange)
                        .glassEffect(.regular, in: .capsule)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }

                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Now")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(summary.currentAction)
                            .font(.subheadline.weight(.semibold))
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("Next")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text("\(summary.nextAction) \(summary.nextTime.formatted(Formatters.time))")
                            .font(.subheadline.weight(.semibold))
                    }
                }

                Divider()

                HStack {
                    VStack(alignment: .leading) {
                        Text("Today's Cost")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(Formatters.signedMoney(summary.todayCost))
                            .font(.title3.weight(.semibold))
                            .fontDesign(.rounded)
                            .foregroundStyle(.green)
                    }
                    Spacer()
                    VStack(alignment: .trailing) {
                        Text("Savings")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(Formatters.signedMoney(summary.todaySavings))
                            .font(.title3.weight(.semibold))
                            .fontDesign(.rounded)
                            .foregroundStyle(.green)
                    }
                }
            }
            .padding()
            .glassEffect(.regular, in: .rect(cornerRadius: 24))
        }
        .buttonStyle(.plain)
    }
}
