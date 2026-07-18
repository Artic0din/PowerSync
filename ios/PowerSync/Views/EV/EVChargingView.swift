//
//  EVChargingView.swift
//  PowerSync
//

import SwiftUI

struct EVChargingView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Label("Wait for Better Rates", systemImage: "clock.fill")
                        .font(.headline)
                        .foregroundStyle(.orange)
                    Text("High grid price (\(Formatters.centsPerKWh(model.snapshot.importCents))) — waiting for cheaper rates.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    HStack {
                        stat("Import", Formatters.centsPerKWh(model.snapshot.importCents), .red)
                        stat("Export", Formatters.centsPerKWh(model.snapshot.exportCents), .green)
                        stat("Surplus", Formatters.kw(0), .primary)
                        stat("Battery", "\(Int(model.snapshot.batteryPercent))%", .blue)
                    }
                }
                .padding(.vertical, 4)
            }

            Section("Vehicle") {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Label(model.vehicle.name, systemImage: "car.fill")
                            .font(.headline)
                        Text("Target \(Int(model.vehicle.targetPercent))% by \(model.vehicle.deadline.formatted(Formatters.time))")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        if model.vehicle.isCharging {
                            Text("Charging \(Formatters.kw(model.vehicle.chargeKW))")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.blue)
                        }
                    }
                    Spacer()
                    Gauge(value: model.vehicle.socPercent, in: 0...100) {
                        Text("SOC")
                    } currentValueLabel: {
                        Text("\(Int(model.vehicle.socPercent))%")
                    }
                    .gaugeStyle(.accessoryCircularCapacity)
                    .tint(.blue)
                    .frame(width: 72, height: 72)
                }
            }

            Section("Smart Charging Modes") {
                ForEach(EVChargingMode.allCases.filter { $0 != .scheduled }) { mode in
                    Toggle(isOn: binding(for: mode)) {
                        Label {
                            VStack(alignment: .leading) {
                                Text(mode.rawValue)
                                Text(mode.subtitle)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: mode.systemImage)
                        }
                    }
                }
            }

            Section("Manual Modes") {
                Toggle(isOn: binding(for: .scheduled)) {
                    Label {
                        VStack(alignment: .leading) {
                            Text(EVChargingMode.scheduled.rawValue)
                            Text(EVChargingMode.scheduled.subtitle)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: EVChargingMode.scheduled.systemImage)
                    }
                }
            }
        }
        .navigationTitle("EV Charging")
    }

    private func binding(for mode: EVChargingMode) -> Binding<Bool> {
        Binding(
            get: { model.evModes[mode] ?? false },
            set: { model.setEVMode(mode, enabled: $0) }
        )
    }

    private func stat(_ title: String, _ value: String, _ tint: Color) -> some View {
        VStack(spacing: 4) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity)
    }
}
