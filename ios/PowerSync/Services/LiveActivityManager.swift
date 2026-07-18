//
//  LiveActivityManager.swift
//  PowerSync
//
//  Starts/updates ActivityKit Live Activities using system APIs only.
//

import ActivityKit
import Foundation

enum LiveActivityManager {
    @MainActor
    static func sync(from model: AppModel) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }

        let state = OptimizationActivityAttributes.ContentState(
            currentAction: model.optimization.currentAction,
            nextAction: model.optimization.nextAction,
            exportKW: max(0, -model.snapshot.gridWatts) / 1000.0,
            todaySavings: model.optimization.todaySavings
        )

        if let existing = Activity<OptimizationActivityAttributes>.activities.first {
            Task {
                await existing.update(ActivityContent(state: state, staleDate: nil))
            }
            return
        }

        let attributes = OptimizationActivityAttributes(siteName: model.batterySystemName)
        do {
            _ = try Activity.request(
                attributes: attributes,
                content: ActivityContent(state: state, staleDate: nil),
                pushType: nil
            )
        } catch {
            // Live Activities may be unavailable in Simulator or when disabled by the user.
        }
    }
}
