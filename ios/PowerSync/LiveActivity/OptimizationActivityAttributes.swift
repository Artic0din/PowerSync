//
//  OptimizationActivityAttributes.swift
//  PowerSync
//

import ActivityKit
import Foundation

struct OptimizationActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable, Sendable {
        var currentAction: String
        var nextAction: String
        var exportKW: Double
        var todaySavings: Double
    }

    var siteName: String
}
