//
//  EnergyFlowSection.swift
//  PowerSync
//
//  Native SwiftUI layout + SF Symbols. No custom drawing frameworks.
//

import SwiftUI

struct EnergyFlowSection: View {
    let snapshot: LiveEnergySnapshot

    var body: some View {
        VStack(spacing: 16) {
            Text("Live Energy Flow")
                .font(.headline)
                .frame(maxWidth: .infinity, alignment: .leading)

            Grid(horizontalSpacing: 16, verticalSpacing: 16) {
                GridRow {
                    Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                    flowNode(
                        title: EnergyFlowNode.solar.title,
                        value: Formatters.kw(snapshot.solarKW),
                        systemImage: EnergyFlowNode.solar.systemImage,
                        tint: .orange
                    )
                    Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                }

                GridRow {
                    flowNode(
                        title: EnergyFlowNode.battery.title,
                        value: "\(snapshot.batteryPercent.formatted(.number.precision(.fractionLength(0))))% · \(snapshot.batteryWatts.formatted(.number.precision(.fractionLength(0)))) W",
                        systemImage: EnergyFlowNode.battery.systemImage,
                        tint: .cyan
                    )
                    flowNode(
                        title: EnergyFlowNode.home.title,
                        value: Formatters.kw(snapshot.homeKW),
                        systemImage: EnergyFlowNode.home.systemImage,
                        tint: .primary,
                        emphasized: true
                    )
                    flowNode(
                        title: EnergyFlowNode.grid.title,
                        value: "\(snapshot.gridWatts.formatted(.number.precision(.fractionLength(0)))) W",
                        systemImage: EnergyFlowNode.grid.systemImage,
                        tint: .secondary
                    )
                }

                GridRow {
                    Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                    flowNode(
                        title: EnergyFlowNode.ev.title,
                        value: "Charging",
                        systemImage: EnergyFlowNode.ev.systemImage,
                        tint: .blue
                    )
                    Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                }
            }
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private func flowNode(
        title: String,
        value: String,
        systemImage: String,
        tint: Color,
        emphasized: Bool = false
    ) -> some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(emphasized ? .largeTitle : .title2)
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(tint)
                .frame(width: emphasized ? 72 : 56, height: emphasized ? 72 : 56)
                .background {
                    Circle()
                        .fill(.quaternary)
                }
            Text(title)
                .font(.caption.weight(.semibold))
            Text(value)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity)
    }
}
