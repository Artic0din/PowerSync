//
//  PricesView.swift
//  PowerSync
//

import Charts
import SwiftUI

struct PricesView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model

        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                tariffChips
                forecastCard
                touCard
                energySummaryCard
            }
            .padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Prices & Tariff")
        .toolbarTitleDisplayMode(.inline)
    }

    private var tariffChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(TariffWindow.allCases) { window in
                    let isActive = model.activeTariffWindows.contains(window)
                    Label(window.rawValue, systemImage: window.systemImage)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .foregroundStyle(isActive ? Color.primary : Color.secondary)
                        .glassEffect(isActive ? .regular : .identity, in: .capsule)
                }
            }
        }
    }

    private var forecastCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Price Forecast")
                .font(.headline)

            HStack {
                labeledStat("Buy Range", buyRangeText, .red)
                labeledStat("Sell Range", sellRangeText, .green)
                labeledStat("Periods", "\(model.priceForecast.count)", .primary)
            }

            Chart {
                ForEach(model.priceForecast) { point in
                    AreaMark(
                        x: .value("Time", point.time),
                        y: .value("Buy", point.importCents)
                    )
                    .foregroundStyle(Color.red.opacity(0.25))

                    LineMark(
                        x: .value("Time", point.time),
                        y: .value("Buy", point.importCents)
                    )
                    .foregroundStyle(.red)
                    .interpolationMethod(.stepStart)

                    LineMark(
                        x: .value("Time", point.time),
                        y: .value("Sell", point.exportCents)
                    )
                    .foregroundStyle(.green)
                    .interpolationMethod(.stepStart)
                }
            }
            .chartForegroundStyleScale([
                "Buy": Color.red,
                "Sell": Color.green
            ])
            .frame(height: 200)
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private var touCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Time-of-Use Schedule")
                .font(.headline)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(Array(model.touSlots.enumerated()), id: \.element.id) { index, slot in
                        VStack(spacing: 6) {
                            Text(slot.time.formatted(Formatters.time))
                                .font(.caption.weight(.semibold))
                            Text("B \(slot.buyCents.formatted(.number.precision(.fractionLength(0))))¢")
                                .font(.caption2)
                                .foregroundStyle(.red)
                            Text("S \(slot.sellCents.formatted(.number.precision(.fractionLength(0))))¢")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .padding(12)
                        .glassEffect(index == 2 ? .regular : .identity, in: .rect(cornerRadius: 16))
                        .overlay {
                            if index == 2 {
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .strokeBorder(.mint, lineWidth: 2)
                            }
                        }
                    }
                }
            }
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private var energySummaryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Energy Summary")
                    .font(.headline)
                Spacer()
                Picker("Period", selection: $model.summaryPeriod) {
                    ForEach(SummaryPeriod.allCases) { period in
                        Text(period.rawValue).tag(period)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 240)
                .onChange(of: model.summaryPeriod) { _, newValue in
                    model.updateSummaryPeriod(newValue)
                }
            }

            HStack(spacing: 12) {
                summaryTile("Generation", Formatters.kwh(model.energyTotals.generationKWh), "sun.max.fill", .orange)
                summaryTile("Export", Formatters.kwh(model.energyTotals.exportKWh), "arrow.up.circle.fill", .green)
            }
        }
        .padding()
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
    }

    private func labeledStat(_ title: String, _ value: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func summaryTile(_ title: String, _ value: String, _ image: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: image)
                .font(.caption)
                .foregroundStyle(tint)
            Text(value)
                .font(.title3.weight(.bold))
                .fontDesign(.rounded)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.quaternary.opacity(0.35), in: .rect(cornerRadius: 16))
    }

    private var buyRangeText: String {
        let values = model.priceForecast.map(\.importCents)
        guard let min = values.min(), let max = values.max() else { return "—" }
        return "\(min.formatted(.number.precision(.fractionLength(0))))–\(max.formatted(.number.precision(.fractionLength(0))))¢"
    }

    private var sellRangeText: String {
        let values = model.priceForecast.map(\.exportCents)
        guard let min = values.min(), let max = values.max() else { return "—" }
        return "\(min.formatted(.number.precision(.fractionLength(0))))–\(max.formatted(.number.precision(.fractionLength(0))))¢"
    }
}
