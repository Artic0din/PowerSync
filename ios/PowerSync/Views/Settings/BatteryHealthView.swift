//
//  BatteryHealthView.swift
//  PowerSync
//

import Charts
import SwiftUI

struct BatteryHealthView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            Section {
                HStack {
                    Gauge(value: model.batteryHealth.socPercent, in: 0...100) {
                        Text("SOC")
                    } currentValueLabel: {
                        Text("\(Int(model.batteryHealth.socPercent))%")
                    }
                    .gaugeStyle(.accessoryCircularCapacity)
                    .tint(.mint)
                    .frame(width: 100, height: 100)

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Charging")
                            .font(.headline)
                        Text("\(model.batteryHealth.powerWatts.formatted(.number.precision(.fractionLength(0)))) W")
                            .font(.title3.weight(.semibold))
                            .fontDesign(.rounded)
                            .foregroundStyle(.mint)
                    }
                    Spacer()
                }
                .padding(.vertical, 8)
            }

            Section("Health") {
                LabeledContent("Health") {
                    Text("\(model.batteryHealth.healthPercent.formatted(.number.precision(.fractionLength(1))))%")
                        .foregroundStyle(.mint)
                        .fontWeight(.semibold)
                }
                LabeledContent("Degradation", value: "\(model.batteryHealth.degradationPercent.formatted(.number.precision(.fractionLength(1))))%")
                LabeledContent("Original", value: Formatters.kwh(model.batteryHealth.originalCapacityKWh))
                LabeledContent("Current", value: Formatters.kwh(model.batteryHealth.currentCapacityKWh))
                LabeledContent("Powerwall Units", value: "\(model.batteryHealth.unitCount)")
                LabeledContent("Cycles", value: "\(model.batteryHealth.cycleCount)")
            }

            Section("Live") {
                LabeledContent("Power", value: "\(model.batteryHealth.powerWatts.formatted(.number.precision(.fractionLength(0)))) W")
                LabeledContent("Temp", value: "\(model.batteryHealth.temperatureC.formatted(.number.precision(.fractionLength(0))))°C")
                LabeledContent("Voltage", value: "\(model.batteryHealth.voltageV.formatted(.number.precision(.fractionLength(1)))) V")
            }

            Section("Capacity over Time") {
                Chart(model.batteryHealth.capacityHistory) { sample in
                    LineMark(
                        x: .value("Month", sample.date),
                        y: .value("Health", sample.healthPercent)
                    )
                    .foregroundStyle(.mint)
                    .interpolationMethod(.catmullRom)

                    AreaMark(
                        x: .value("Month", sample.date),
                        y: .value("Health", sample.healthPercent)
                    )
                    .foregroundStyle(Color.mint.opacity(0.2))
                }
                .chartYScale(domain: 70...100)
                .frame(height: 160)
            }

            Section {
                Button {
                    Task { await model.rescanBattery() }
                } label: {
                    Label("Rescan Battery (TEDAPI)", systemImage: "viewfinder")
                }
            } footer: {
                Text("Last scan: \(model.batteryHealth.lastScan.formatted(Formatters.timeWithDay))")
            }
        }
        .navigationTitle("Battery Health")
    }
}
