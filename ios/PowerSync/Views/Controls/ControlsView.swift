//
//  ControlsView.swift
//  PowerSync
//

import SwiftUI

struct ControlsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model

        List {
            Section("Force Modes") {
                Picker("Force Charge Duration", selection: $model.forceChargeDuration) {
                    ForEach(ForceDurationMinutes.allCases) { duration in
                        Text(duration.label).tag(duration)
                    }
                }
                Button {
                    Task { await model.forceCharge() }
                } label: {
                    Label("Force Charge", systemImage: "bolt.fill")
                }

                Picker("Force Discharge Duration", selection: $model.forceDischargeDuration) {
                    ForEach(ForceDurationMinutes.allCases) { duration in
                        Text(duration.label).tag(duration)
                    }
                }
                Button {
                    Task { await model.forceDischarge() }
                } label: {
                    Label("Force Discharge", systemImage: "bolt.badge.automatic")
                }
                .tint(.orange)

                Button {
                    Task { await model.restoreNormal() }
                } label: {
                    Label("Restore Normal", systemImage: "arrow.uturn.backward.circle")
                }
            }

            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Backup Reserve")
                        Spacer()
                        Text("\(Int(model.backupReservePercent))%")
                            .fontDesign(.rounded)
                            .foregroundStyle(.secondary)
                    }
                    Gauge(value: model.backupReservePercent, in: 0...100) {
                        Text("Reserve")
                    } currentValueLabel: {
                        Text("\(Int(model.backupReservePercent))%")
                    }
                    .gaugeStyle(.accessoryCircularCapacity)
                    .tint(.mint)
                    .frame(maxWidth: .infinity)

                    Slider(
                        value: $model.backupReservePercent,
                        in: 0...100,
                        step: 1
                    ) {
                        Text("Backup Reserve")
                    } minimumValueLabel: {
                        Text("0%")
                    } maximumValueLabel: {
                        Text("100%")
                    }
                    .onChange(of: model.backupReservePercent) { _, newValue in
                        Task { await model.setBackupReserve(newValue) }
                    }
                }
            } header: {
                Text("Backup Reserve")
            } footer: {
                Text("Percentage of battery capacity held for outages.")
            }

            Section("Operation Mode") {
                Picker("Mode", selection: $model.operationMode) {
                    ForEach(OperationMode.allCases) { mode in
                        Label(mode.rawValue, systemImage: mode.systemImage).tag(mode)
                    }
                }
                .pickerStyle(.inline)
            }

            Section("Grid") {
                Picker("Grid Export", selection: $model.gridExportRule) {
                    ForEach(GridExportRule.allCases) { rule in
                        Text(rule.rawValue).tag(rule)
                    }
                }
                Toggle("Grid Charging", systemImage: "powerplug.fill", isOn: $model.gridChargingEnabled)
                Toggle("Storm Watch", systemImage: "cloud.bolt.fill", isOn: $model.stormWatchEnabled)

                Button(role: .destructive) {
                    Task { await model.goOffGrid() }
                } label: {
                    Label("Go Off-Grid", systemImage: "power")
                }

                Button {
                    Task { await model.reconnectGrid() }
                } label: {
                    Label("Reconnect to Grid", systemImage: "arrow.triangle.2.circlepath")
                }
            } footer: {
                Text("Off-grid requires battery SOC above the safety floor (default 20%).")
            }

            Section("Shortcuts") {
                NavigationLink {
                    EVChargingView()
                } label: {
                    Label("EV Charging", systemImage: "car.fill")
                }
                NavigationLink {
                    PricesView()
                } label: {
                    Label("Prices & Tariff", systemImage: "chart.xyaxis.line")
                }
                NavigationLink {
                    OptimizationView()
                } label: {
                    Label("Smart Optimization", systemImage: "brain.head.profile")
                }
            }
        }
        .navigationTitle("Controls")
    }
}
