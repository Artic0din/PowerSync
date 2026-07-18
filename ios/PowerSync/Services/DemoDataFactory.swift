//
//  DemoDataFactory.swift
//  PowerSync
//
//  Deterministic demo data matching PowerSync integration semantics.
//

import Foundation

enum DemoDataFactory {
    private static let calendar = Calendar.current

    static func makeSnapshot(now: Date = .now) -> LiveEnergySnapshot {
        LiveEnergySnapshot(
            solarKW: 2.1,
            homeKW: 1.8,
            batteryPercent: 45,
            batteryWatts: 393,
            gridWatts: 0,
            importCents: 31.0,
            exportCents: 0.0,
            providerName: "GloBird",
            weatherSummary: "Clear Sky",
            temperatureC: 20,
            humidityPercent: 91
        )
    }

    static func makeOptimization(now: Date = .now) -> OptimizationSummary {
        let next = calendar.date(bySettingHour: 10, minute: 0, second: 0, of: now)
            ?? now.addingTimeInterval(3600)
        return OptimizationSummary(
            status: .monitoring,
            mode: .costMinimization,
            currentAction: "Self-Consumption",
            currentPowerKW: 0.4,
            nextAction: "Charging",
            nextTime: next,
            nextPowerKW: 9.8,
            todayCost: -0.04,
            todaySavings: 7.31,
            chargeKWh: 10.8,
            dischargeKWh: 3.6,
            gridImportKWh: 10.8,
            gridExportKWh: 0.5,
            lastOptimized: now.addingTimeInterval(-240)
        )
    }

    static func makeSchedule(now: Date = .now) -> [ScheduleSlot] {
        let startOfDay = calendar.startOfDay(for: now)
        func slot(h1: Int, m1: Int, h2: Int, m2: Int, action: ScheduleActionKind, kw: Double, soc: Double) -> ScheduleSlot {
            let start = calendar.date(bySettingHour: h1, minute: m1, second: 0, of: startOfDay) ?? now
            let end = calendar.date(bySettingHour: h2, minute: m2, second: 0, of: startOfDay) ?? now
            return ScheduleSlot(start: start, end: end, action: action, powerKW: kw, socPercent: soc)
        }
        return [
            slot(h1: 7, m1: 30, h2: 10, m2: 0, action: .selfConsumption, kw: 0.4, soc: 36),
            slot(h1: 10, m1: 0, h2: 12, m2: 50, action: .charge, kw: 9.8, soc: 97),
            slot(h1: 12, m1: 55, h2: 14, m2: 0, action: .charge, kw: 2.0, soc: 100),
            slot(h1: 14, m1: 0, h2: 17, m2: 25, action: .idle, kw: 0, soc: 100),
            slot(h1: 17, m1: 25, h2: 19, m2: 0, action: .export, kw: 2.3, soc: 85),
            slot(h1: 19, m1: 0, h2: 23, m2: 0, action: .selfConsumption, kw: 0.6, soc: 45)
        ]
    }

    static func makePriceForecast(now: Date = .now) -> [PricePoint] {
        let start = calendar.startOfDay(for: now)
        return (0..<48).compactMap { index in
            guard let time = calendar.date(byAdding: .minute, value: index * 30, to: start) else { return nil }
            let hour = calendar.component(.hour, from: time)
            let importCents: Double
            if hour >= 16 && hour < 21 {
                importCents = 48
            } else if hour >= 0 && hour < 4 {
                importCents = 8
            } else {
                importCents = 31
            }
            let exportCents = (hour >= 10 && hour < 14) ? 10.0 : 0.0
            return PricePoint(time: time, importCents: importCents, exportCents: exportCents)
        }
    }

    static func makeTOUSlots(now: Date = .now) -> [TOUSlot] {
        let start = calendar.startOfDay(for: now)
        return (13..<20).compactMap { halfHour in
            guard let time = calendar.date(byAdding: .minute, value: halfHour * 30, to: start) else { return nil }
            return TOUSlot(time: time, buyCents: 31, sellCents: 0)
        }
    }

    static func makeVehicle(now: Date = .now) -> EVVehicle {
        let deadline = calendar.date(bySettingHour: 7, minute: 30, second: 0, of: now.addingTimeInterval(86_400))
            ?? now.addingTimeInterval(86_400)
        return EVVehicle(
            name: "Tesla Model Y",
            socPercent: 62,
            targetPercent: 80,
            deadline: deadline,
            chargeKW: 7.2,
            isCharging: true
        )
    }

    static func makeAutomations() -> [AutomationRule] {
        [
            AutomationRule(
                title: "AEMO Price Spike",
                triggerSummary: "When price > $1.00",
                actionSummary: "Force export battery",
                systemImage: "chart.line.uptrend.xyaxis",
                isEnabled: true
            ),
            AutomationRule(
                title: "Cheap Import Window",
                triggerSummary: "When import < 8¢",
                actionSummary: "Charge battery + EV",
                systemImage: "dollarsign.circle.fill",
                isEnabled: true
            ),
            AutomationRule(
                title: "Solar Surplus",
                triggerSummary: "When surplus > 2 kW",
                actionSummary: "Charge EV",
                systemImage: "sun.max.fill",
                isEnabled: true
            ),
            AutomationRule(
                title: "Grid Outage",
                triggerSummary: "When grid offline",
                actionSummary: "Hold 30% reserve",
                systemImage: "bolt.horizontal.fill",
                isEnabled: false
            ),
            AutomationRule(
                title: "Evening Peak",
                triggerSummary: "At 4:00 pm",
                actionSummary: "Self-Consumption",
                systemImage: "clock.fill",
                isEnabled: true
            )
        ]
    }

    static func makeBatteryHealth(now: Date = .now) -> BatteryHealthReport {
        let history: [CapacitySample] = (0..<12).compactMap { monthsAgo in
            guard let date = calendar.date(byAdding: .month, value: -(11 - monthsAgo), to: now) else { return nil }
            let health = 100.0 - Double(monthsAgo) * 0.42
            return CapacitySample(date: date, healthPercent: health)
        }
        return BatteryHealthReport(
            socPercent: 45,
            powerWatts: 393,
            healthPercent: 94.9,
            degradationPercent: 5.0,
            originalCapacityKWh: 40.5,
            currentCapacityKWh: 38.5,
            unitCount: 2,
            cycleCount: 812,
            temperatureC: 24,
            voltageV: 51.2,
            lastScan: now.addingTimeInterval(-3600),
            capacityHistory: history
        )
    }

    static func makeEnergyTotals(for period: SummaryPeriod) -> EnergyPeriodTotals {
        switch period {
        case .day: EnergyPeriodTotals(generationKWh: 1.4, exportKWh: 0.027)
        case .week: EnergyPeriodTotals(generationKWh: 42.0, exportKWh: 6.2)
        case .month: EnergyPeriodTotals(generationKWh: 180.0, exportKWh: 28.0)
        case .year: EnergyPeriodTotals(generationKWh: 2100.0, exportKWh: 340.0)
        }
    }
}
