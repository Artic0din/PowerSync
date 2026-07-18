//
//  OptimizationView.swift
//  PowerSync
//

import Charts
import SwiftUI

struct OptimizationView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model

        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Picker("Mode", selection: $model.optimization.mode) {
                    ForEach(OptimizationMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)

                statusCard
                summaryCard
                planCard

                Button {
                    Task { await model.refreshOptimization() }
                } label: {
                    Label("Refresh Now", systemImage: "arrow.clockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
            .padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Smart Optimization")
        .toolbarTitleDisplayMode(.inline)
    }

    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                LabeledContent("Status") {
                    Text(model.optimization.status.rawValue)
                        .foregroundStyle(.orange)
                        .fontWeight(.semibold)
                }
                LabeledContent("Mode") {
                    Text(model.optimization.mode.detailTitle)
                        .fontWeight(.semibold)
                }
            }

            if model.optimization.status == .monitoring {
                Label("Monitoring — actions are logged but not executed.", systemImage: "eye.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.orange.opacity(0.12), in: .rect(cornerRadius: 12))
            }

            HStack {
                actionColumn(
                    title: "Current",
                    action: model.optimization.currentAction,
                    detail: Formatters.kw(model.optimization.currentPowerKW),
                    tint: .blue
                )
                Image(systemName: "arrow.right")
                    .foregroundStyle(.secondary)
                actionColumn(
                    title: "Next",
                    action: model.optimization.nextAction,
                    detail: "\(model.optimization.nextTime.formatted(Formatters.time)) · \(Formatters.kw(model.optimization.nextPowerKW))",
                    tint: .green
                )
            }
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private func actionColumn(title: String, action: String, detail: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(action)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Today's Summary")
                .font(.headline)

            HStack {
                VStack(alignment: .leading) {
                    Text("Cost")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(Formatters.signedMoney(model.optimization.todayCost))
                        .font(.title2.weight(.bold))
                        .fontDesign(.rounded)
                        .foregroundStyle(.yellow)
                }
                Spacer()
                VStack(alignment: .trailing) {
                    Text("Savings")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(Formatters.signedMoney(model.optimization.todaySavings))
                        .font(.title2.weight(.bold))
                        .fontDesign(.rounded)
                        .foregroundStyle(.green)
                }
            }

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                summaryTile("Charge", Formatters.kwh(model.optimization.chargeKWh), "battery.100.bolt", .blue)
                summaryTile("Discharge", Formatters.kwh(model.optimization.dischargeKWh), "battery.25", .green)
                summaryTile("Grid Import", Formatters.kwh(model.optimization.gridImportKWh), "arrow.down.circle.fill", .red)
                summaryTile("Grid Export", Formatters.kwh(model.optimization.gridExportKWh), "arrow.up.circle.fill", .green)
            }
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private func summaryTile(_ title: String, _ value: String, _ image: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: image)
                .font(.caption)
                .foregroundStyle(tint)
            Text(value)
                .font(.headline)
                .fontDesign(.rounded)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: .rect(cornerRadius: 14))
    }

    private var planCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("24-Hour Plan")
                .font(.headline)

            Chart {
                ForEach(model.schedule) { slot in
                    AreaMark(
                        x: .value("Time", slot.start),
                        y: .value("SOC", slot.socPercent)
                    )
                    .foregroundStyle(Color.cyan.opacity(0.35))
                    .interpolationMethod(.linear)

                    LineMark(
                        x: .value("Time", slot.start),
                        y: .value("SOC", slot.socPercent)
                    )
                    .foregroundStyle(Color.cyan)
                }

                ForEach(model.priceForecast) { point in
                    LineMark(
                        x: .value("Time", point.time),
                        y: .value("Price", point.importCents)
                    )
                    .foregroundStyle(Color.red.opacity(0.7))
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
            }
            .chartYAxisLabel("SOC % / ¢")
            .frame(height: 180)

            ForEach(model.schedule.filter { $0.action != .idle }) { slot in
                HStack {
                    Label(slot.action.title, systemImage: slot.action.systemImage)
                        .foregroundStyle(color(for: slot.action))
                    Spacer()
                    Text("\(slot.start.formatted(Formatters.time))–\(slot.end.formatted(Formatters.time))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("\(Formatters.kw(slot.powerKW)) · \(slot.socPercent.formatted(.number.precision(.fractionLength(0))))%")
                        .font(.caption.weight(.medium))
                }
                .padding(.vertical, 4)
            }

            Text("Last optimized: \(model.optimization.lastOptimized.formatted(Formatters.timeWithDay))")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private func color(for action: ScheduleActionKind) -> Color {
        switch action {
        case .charge: .green
        case .export, .discharge: .orange
        case .selfConsumption: .blue
        case .idle: .secondary
        }
    }
}
