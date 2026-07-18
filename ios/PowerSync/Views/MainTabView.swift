//
//  MainTabView.swift
//  PowerSync
//
//  System TabView — Liquid Glass tab bar is automatic on iOS 26+.
//

import SwiftUI

struct MainTabView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        TabView {
            Tab("Dashboard", systemImage: "gauge.with.dots.needle.67percent") {
                NavigationStack {
                    DashboardView()
                }
            }

            Tab("Controls", systemImage: "slider.horizontal.3") {
                NavigationStack {
                    ControlsView()
                }
            }

            Tab("Automations", systemImage: "bolt.badge.automatic") {
                NavigationStack {
                    AutomationsView()
                }
            }

            Tab("Settings", systemImage: "gearshape.fill") {
                NavigationStack {
                    SettingsView()
                }
            }
        }
        .tabBarMinimizeBehavior(.onScrollDown)
        .sensoryFeedback(.selection, trigger: model.statusMessage)
        .alert(
            "Status",
            isPresented: Binding(
                get: { model.statusMessage != nil },
                set: { if !$0 { model.statusMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { model.statusMessage = nil }
        } message: {
            Text(model.statusMessage ?? "")
        }
    }
}
