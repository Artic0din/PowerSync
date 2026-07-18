//
//  RootView.swift
//  PowerSync
//

import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if model.hasCompletedOnboarding {
                MainTabView()
            } else {
                OnboardingView()
            }
        }
        .animation(.smooth, value: model.hasCompletedOnboarding)
    }
}
