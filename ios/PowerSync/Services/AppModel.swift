//
//  AppModel.swift
//  PowerSync
//
//  App-wide observable state using the Observation framework.
//

import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    // MARK: - Connection

    var hasCompletedOnboarding: Bool
    var usesDemoMode: Bool
    var homeAssistantURL: String
    var connectionError: String?
    var isBusy = false

    // MARK: - Live data

    var snapshot: LiveEnergySnapshot
    var optimization: OptimizationSummary
    var schedule: [ScheduleSlot]
    var priceForecast: [PricePoint]
    var touSlots: [TOUSlot]
    var activeTariffWindows: Set<TariffWindow>
    var summaryPeriod: SummaryPeriod = .day
    var energyTotals: EnergyPeriodTotals

    // MARK: - Controls

    var backupReservePercent: Double = 20
    var operationMode: OperationMode = .timeBased
    var gridExportRule: GridExportRule = .solarOnly
    var gridChargingEnabled = true
    var stormWatchEnabled = true
    var forceChargeDuration: ForceDurationMinutes = .m30
    var forceDischargeDuration: ForceDurationMinutes = .m30
    var statusMessage: String?

    // MARK: - EV

    var vehicle: EVVehicle
    var evModes: [EVChargingMode: Bool]

    // MARK: - Automations / health / settings

    var automations: [AutomationRule]
    var batteryHealth: BatteryHealthReport
    var batterySystemName = "Tesla Powerwall"
    var isBatteryAutoDetected = true

    private let client = HomeAssistantClient()
    private let urlAccount = "ha.url"
    private let tokenAccount = "ha.token"
    private let onboardingKey = "powersync.hasCompletedOnboarding"
    private let demoKey = "powersync.usesDemoMode"

    init() {
        let defaults = UserDefaults.standard
        hasCompletedOnboarding = defaults.bool(forKey: onboardingKey)
        usesDemoMode = defaults.object(forKey: demoKey) as? Bool ?? true
        homeAssistantURL = KeychainStore.get(account: urlAccount) ?? ""
        snapshot = DemoDataFactory.makeSnapshot()
        optimization = DemoDataFactory.makeOptimization()
        schedule = DemoDataFactory.makeSchedule()
        priceForecast = DemoDataFactory.makePriceForecast()
        touSlots = DemoDataFactory.makeTOUSlots()
        activeTariffWindows = [.happyHour, .aemoSpikeWatch, .zeroHero]
        energyTotals = DemoDataFactory.makeEnergyTotals(for: .day)
        vehicle = DemoDataFactory.makeVehicle()
        evModes = [
            .smartSchedule: true,
            .solarSurplus: false,
            .priceLevel: false,
            .scheduled: false
        ]
        automations = DemoDataFactory.makeAutomations()
        batteryHealth = DemoDataFactory.makeBatteryHealth()

        if !usesDemoMode, let token = KeychainStore.get(account: tokenAccount), !homeAssistantURL.isEmpty {
            Task { await connect(url: homeAssistantURL, token: token, demo: false) }
        } else {
            reloadDemo()
        }
    }

    // MARK: - Onboarding / connection

    func enterDemoMode() {
        usesDemoMode = true
        UserDefaults.standard.set(true, forKey: demoKey)
        hasCompletedOnboarding = true
        UserDefaults.standard.set(true, forKey: onboardingKey)
        reloadDemo()
        connectionError = nil
    }

    func connect(url: String, token: String, demo: Bool) async {
        isBusy = true
        connectionError = nil
        defer { isBusy = false }

        if demo {
            enterDemoMode()
            return
        }

        do {
            try await client.configure(baseURLString: url, token: token)
            try await client.ping()
            homeAssistantURL = url
            KeychainStore.set(url, account: urlAccount)
            KeychainStore.set(token, account: tokenAccount)
            usesDemoMode = false
            UserDefaults.standard.set(false, forKey: demoKey)
            hasCompletedOnboarding = true
            UserDefaults.standard.set(true, forKey: onboardingKey)
            try await refreshFromHomeAssistant()
        } catch {
            connectionError = error.localizedDescription
            // Keep the UI usable with demo data if live fetch fails.
            usesDemoMode = true
            UserDefaults.standard.set(true, forKey: demoKey)
            reloadDemo()
        }
    }

    func disconnect() async {
        await client.clear()
        KeychainStore.delete(account: tokenAccount)
        usesDemoMode = true
        UserDefaults.standard.set(true, forKey: demoKey)
        reloadDemo()
    }

    // MARK: - Refresh

    func refresh() async {
        if usesDemoMode {
            reloadDemo()
            statusMessage = "Demo data refreshed"
            return
        }
        do {
            try await refreshFromHomeAssistant()
            statusMessage = "Synced with Home Assistant"
        } catch {
            connectionError = error.localizedDescription
        }
    }

    func refreshOptimization() async {
        await refresh()
        optimization.lastOptimized = .now
    }

    private func reloadDemo() {
        let now = Date.now
        snapshot = DemoDataFactory.makeSnapshot(now: now)
        optimization = DemoDataFactory.makeOptimization(now: now)
        schedule = DemoDataFactory.makeSchedule(now: now)
        priceForecast = DemoDataFactory.makePriceForecast(now: now)
        touSlots = DemoDataFactory.makeTOUSlots(now: now)
        vehicle = DemoDataFactory.makeVehicle(now: now)
        batteryHealth = DemoDataFactory.makeBatteryHealth(now: now)
        energyTotals = DemoDataFactory.makeEnergyTotals(for: summaryPeriod)
        SharedSnapshotWriter.write(from: self)
    }

    private func refreshFromHomeAssistant() async throws {
        let states = try await client.fetchStates()
        apply(states: states)
        SharedSnapshotWriter.write(from: self)
    }

    /// Maps common PowerSync entity IDs when present; falls back to demo values.
    private func apply(states: [HomeAssistantState]) {
        func state(for suffix: String) -> HomeAssistantState? {
            states.first { $0.entityId.hasSuffix(suffix) || $0.entityId.contains(suffix) }
        }
        func double(from entity: HomeAssistantState?) -> Double? {
            guard let raw = entity?.state, let value = Double(raw) else { return nil }
            return value
        }

        var next = snapshot
        if let v = double(from: state(for: "solar_power")) { next.solarKW = v / 1000.0 }
        if let v = double(from: state(for: "home_power")) { next.homeKW = abs(v) / 1000.0 }
        if let v = double(from: state(for: "battery_soc")) { next.batteryPercent = v }
        if let v = double(from: state(for: "battery_power")) { next.batteryWatts = v }
        if let v = double(from: state(for: "grid_power")) { next.gridWatts = v }
        if let v = double(from: state(for: "general_price")) { next.importCents = v * 100.0 }
        if let v = double(from: state(for: "feed_in_price")) { next.exportCents = v * 100.0 }
        snapshot = next
    }

    // MARK: - Controls (HA services when connected)

    func forceCharge() async {
        await call(service: "force_charge", data: ["duration": String(forceChargeDuration.rawValue)])
        statusMessage = "Force charge for \(forceChargeDuration.label)"
    }

    func forceDischarge() async {
        await call(service: "force_discharge", data: ["duration": String(forceDischargeDuration.rawValue)])
        statusMessage = "Force discharge for \(forceDischargeDuration.label)"
    }

    func restoreNormal() async {
        await call(service: "restore_normal")
        statusMessage = "Restored normal operation"
    }

    func setBackupReserve(_ value: Double) async {
        backupReservePercent = value.rounded()
        await call(service: "set_backup_reserve", data: ["percent": String(Int(backupReservePercent))])
    }

    func goOffGrid() async {
        await call(service: "powerwall_go_off_grid")
        statusMessage = "Requested off-grid"
    }

    func reconnectGrid() async {
        await call(service: "powerwall_reconnect_grid")
        statusMessage = "Requested grid reconnect"
    }

    func rescanBattery() async {
        batteryHealth.lastScan = .now
        statusMessage = "Battery health scan recorded"
        SharedSnapshotWriter.write(from: self)
    }

    private func call(service: String, data: [String: String] = [:]) async {
        guard !usesDemoMode else { return }
        do {
            try await client.callService(domain: "power_sync", service: service, data: data)
        } catch {
            connectionError = error.localizedDescription
        }
    }

    func updateSummaryPeriod(_ period: SummaryPeriod) {
        summaryPeriod = period
        energyTotals = DemoDataFactory.makeEnergyTotals(for: period)
    }

    func setEVMode(_ mode: EVChargingMode, enabled: Bool) {
        evModes[mode] = enabled
    }

    func setAutomation(_ rule: AutomationRule, enabled: Bool) {
        guard let index = automations.firstIndex(where: { $0.id == rule.id }) else { return }
        automations[index].isEnabled = enabled
    }
}
