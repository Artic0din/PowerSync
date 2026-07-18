//
//  EnergyModels.swift
//  PowerSync
//
//  Domain models aligned with the Home Assistant PowerSync integration.
//

import Foundation

enum EnergyFlowNode: String, Sendable, CaseIterable, Identifiable {
    case solar
    case home
    case battery
    case grid
    case ev

    var id: String { rawValue }

    var title: String {
        switch self {
        case .solar: "Solar"
        case .home: "Home"
        case .battery: "Battery"
        case .grid: "Grid"
        case .ev: "EV"
        }
    }

    var systemImage: String {
        switch self {
        case .solar: "sun.max.fill"
        case .home: "house.fill"
        case .battery: "battery.100.bolt"
        case .grid: "bolt.horizontal.fill"
        case .ev: "car.fill"
        }
    }
}

struct LiveEnergySnapshot: Sendable, Equatable {
    var solarKW: Double
    var homeKW: Double
    var batteryPercent: Double
    var batteryWatts: Double
    var gridWatts: Double
    var importCents: Double
    var exportCents: Double
    var providerName: String
    var weatherSummary: String
    var temperatureC: Double
    var humidityPercent: Double
}

enum OptimizationMode: String, CaseIterable, Identifiable, Sendable {
    case costMinimization = "Cost Min"
    case profitMax = "Profit Max"
    case chargeByTime = "Charge By Time"

    var id: String { rawValue }

    var detailTitle: String {
        switch self {
        case .costMinimization: "Cost Minimization"
        case .profitMax: "Profit Max"
        case .chargeByTime: "Charge By Time"
        }
    }
}

enum OptimizationStatus: String, Sendable {
    case monitoring = "Monitoring"
    case active = "Active"
    case disabled = "Disabled"
}

struct OptimizationSummary: Sendable, Equatable {
    var status: OptimizationStatus
    var mode: OptimizationMode
    var currentAction: String
    var currentPowerKW: Double
    var nextAction: String
    var nextTime: Date
    var nextPowerKW: Double
    var todayCost: Double
    var todaySavings: Double
    var chargeKWh: Double
    var dischargeKWh: Double
    var gridImportKWh: Double
    var gridExportKWh: Double
    var lastOptimized: Date
}

enum ScheduleActionKind: String, Sendable {
    case idle
    case charge
    case discharge
    case export
    case selfConsumption = "self_consumption"

    var title: String {
        switch self {
        case .idle: "Idle"
        case .charge: "Charging"
        case .discharge: "Discharging"
        case .export: "Exporting"
        case .selfConsumption: "Self-Consumption"
        }
    }

    var systemImage: String {
        switch self {
        case .idle: "pause.circle.fill"
        case .charge: "bolt.fill"
        case .discharge: "battery.25"
        case .export: "arrow.up.circle.fill"
        case .selfConsumption: "arrow.triangle.2.circlepath"
        }
    }
}

struct ScheduleSlot: Identifiable, Sendable, Equatable {
    var id: UUID = UUID()
    var start: Date
    var end: Date
    var action: ScheduleActionKind
    var powerKW: Double
    var socPercent: Double
}

struct PricePoint: Identifiable, Sendable, Equatable {
    var id: UUID = UUID()
    var time: Date
    var importCents: Double
    var exportCents: Double
}

struct TOUSlot: Identifiable, Sendable, Equatable {
    var id: UUID = UUID()
    var time: Date
    var buyCents: Double
    var sellCents: Double
}

enum TariffWindow: String, Sendable, CaseIterable, Identifiable {
    case happyHour = "Happy Hour"
    case aemoSpikeWatch = "AEMO Spike Watch"
    case zeroHero = "ZeroHero"

    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .happyHour: "party.popper.fill"
        case .aemoSpikeWatch: "exclamationmark.triangle.fill"
        case .zeroHero: "star.circle.fill"
        }
    }
}

enum OperationMode: String, CaseIterable, Identifiable, Sendable {
    case timeBased = "Time-Based (TOU)"
    case selfConsumption = "Self-Consumption"
    case backupOnly = "Backup-Only"

    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .timeBased: "clock.fill"
        case .selfConsumption: "house.fill"
        case .backupOnly: "shield.fill"
        }
    }
}

enum GridExportRule: String, CaseIterable, Identifiable, Sendable {
    case never = "Never"
    case solarOnly = "Solar Only"
    case solarAndBattery = "Solar + Battery"

    var id: String { rawValue }
}

enum ForceDurationMinutes: Int, CaseIterable, Identifiable, Sendable {
    case m15 = 15
    case m30 = 30
    case m45 = 45
    case m60 = 60
    case m90 = 90
    case m120 = 120
    case m180 = 180
    case m240 = 240

    var id: Int { rawValue }

    var label: String {
        if rawValue < 60 { return "\(rawValue) min" }
        let hours = Double(rawValue) / 60.0
        return hours == floor(hours) ? "\(Int(hours)) hr" : String(format: "%.1f hr", hours)
    }
}

struct EVVehicle: Sendable, Equatable {
    var name: String
    var socPercent: Double
    var targetPercent: Double
    var deadline: Date
    var chargeKW: Double
    var isCharging: Bool
}

enum EVChargingMode: String, CaseIterable, Identifiable, Sendable {
    case smartSchedule = "Smart Schedule"
    case solarSurplus = "Solar Surplus"
    case priceLevel = "Price-Level"
    case scheduled = "Scheduled Charging"

    var id: String { rawValue }

    var subtitle: String {
        switch self {
        case .smartSchedule: "Auto-schedule by price and solar"
        case .solarSurplus: "Charge from export surplus"
        case .priceLevel: "Recovery and opportunity charging"
        case .scheduled: "Time window plus max price"
        }
    }

    var systemImage: String {
        switch self {
        case .smartSchedule: "calendar.badge.clock"
        case .solarSurplus: "sun.max.fill"
        case .priceLevel: "dollarsign.circle.fill"
        case .scheduled: "alarm.fill"
        }
    }
}

struct AutomationRule: Identifiable, Sendable, Equatable {
    var id: UUID = UUID()
    var title: String
    var triggerSummary: String
    var actionSummary: String
    var systemImage: String
    var isEnabled: Bool
}

struct BatteryHealthReport: Sendable, Equatable {
    var socPercent: Double
    var powerWatts: Double
    var healthPercent: Double
    var degradationPercent: Double
    var originalCapacityKWh: Double
    var currentCapacityKWh: Double
    var unitCount: Int
    var cycleCount: Int
    var temperatureC: Double
    var voltageV: Double
    var lastScan: Date
    var capacityHistory: [CapacitySample]
}

struct CapacitySample: Identifiable, Sendable, Equatable {
    var id: UUID = UUID()
    var date: Date
    var healthPercent: Double
}

struct EnergyPeriodTotals: Sendable, Equatable {
    var generationKWh: Double
    var exportKWh: Double
}

enum SummaryPeriod: String, CaseIterable, Identifiable, Sendable {
    case day = "Day"
    case week = "Week"
    case month = "Month"
    case year = "Year"

    var id: String { rawValue }
}

struct ConnectionConfiguration: Sendable, Equatable {
    var homeAssistantURL: String
    var longLivedToken: String
    var usesDemoMode: Bool
}
