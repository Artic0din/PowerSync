//
//  DashboardView.swift
//  PowerSync
//

import SwiftUI

struct DashboardView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                weatherRow
                priceTiles
                EnergyFlowSection(snapshot: model.snapshot)
                OptimizationGlanceCard(summary: model.optimization)
                quickActions
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("PowerSync")
        .toolbarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await model.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .refreshable {
            await model.refresh()
        }
    }

    private var weatherRow: some View {
        LabeledContent {
            Text("Humidity \(model.snapshot.humidityPercent.formatted(.number.precision(.fractionLength(0))))%")
                .foregroundStyle(.secondary)
        } label: {
            Label(
                "\(model.snapshot.temperatureC.formatted(.number.precision(.fractionLength(0))))° \(model.snapshot.weatherSummary)",
                systemImage: "sun.max.fill"
            )
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 20))
    }

    private var priceTiles: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.snapshot.providerName.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            HStack(spacing: 12) {
                MetricTile(
                    title: "Import",
                    value: Formatters.centsPerKWh(model.snapshot.importCents),
                    systemImage: "arrow.down.circle.fill",
                    tint: .red
                )
                MetricTile(
                    title: "Feed-in",
                    value: Formatters.centsPerKWh(model.snapshot.exportCents),
                    systemImage: "arrow.up.circle.fill",
                    tint: .green
                )
            }
        }
    }

    private var quickActions: some View {
        GlassEffectContainer {
            HStack(spacing: 12) {
                Button {
                    Task { await model.forceCharge() }
                } label: {
                    Label("Force Charge", systemImage: "bolt.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.blue)

                Button {
                    Task { await model.forceDischarge() }
                } label: {
                    Label("Force Discharge", systemImage: "bolt.badge.automatic")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.orange)

                NavigationLink {
                    ControlsView()
                } label: {
                    Label("Backup", systemImage: "shield.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.green)
            }
        }
    }
}

private struct MetricTile: View {
    let title: String
    let value: String
    let systemImage: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: systemImage)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(tint)
            Text(value)
                .font(.title2.weight(.semibold))
                .fontDesign(.rounded)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 20))
    }
}
