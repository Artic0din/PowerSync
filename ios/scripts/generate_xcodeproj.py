#!/usr/bin/env python3
"""Generate PowerSync.xcodeproj/project.pbxproj for the native SwiftUI app."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "PowerSync.xcodeproj" / "project.pbxproj"

APP_SOURCES = [
    "PowerSync/PowerSyncApp.swift",
    "PowerSync/Models/EnergyModels.swift",
    "PowerSync/Services/AppModel.swift",
    "PowerSync/Services/DemoDataFactory.swift",
    "PowerSync/Services/Formatters.swift",
    "PowerSync/Services/HomeAssistantClient.swift",
    "PowerSync/Services/KeychainStore.swift",
    "PowerSync/Services/LiveActivityManager.swift",
    "PowerSync/Services/SharedSnapshotWriter.swift",
    "PowerSync/LiveActivity/OptimizationActivityAttributes.swift",
    "PowerSync/Views/RootView.swift",
    "PowerSync/Views/MainTabView.swift",
    "PowerSync/Views/Dashboard/DashboardView.swift",
    "PowerSync/Views/Dashboard/EnergyFlowSection.swift",
    "PowerSync/Views/Dashboard/OptimizationGlanceCard.swift",
    "PowerSync/Views/Optimization/OptimizationView.swift",
    "PowerSync/Views/Prices/PricesView.swift",
    "PowerSync/Views/Controls/ControlsView.swift",
    "PowerSync/Views/EV/EVChargingView.swift",
    "PowerSync/Views/Automations/AutomationsView.swift",
    "PowerSync/Views/Settings/SettingsView.swift",
    "PowerSync/Views/Settings/ConnectionSettingsView.swift",
    "PowerSync/Views/Settings/BatteryHealthView.swift",
    "PowerSync/Views/Onboarding/OnboardingView.swift",
]

WIDGET_SOURCES = [
    "PowerSyncWidgets/PowerSyncWidgetsBundle.swift",
    "PowerSyncWidgets/SharedSnapshot.swift",
    "PowerSyncWidgets/BatteryGaugeWidget.swift",
    "PowerSyncWidgets/ImportPriceWidget.swift",
    "PowerSyncWidgets/StatusLockScreenWidget.swift",
    "PowerSyncWidgets/OptimizationLiveActivity.swift",
]


def hid(name: str) -> str:
    digest = hashlib.md5(name.encode()).hexdigest()[:24].upper()
    return digest


def file_ref(path: str) -> str:
    return hid(f"fileref:{path}")


def build_file(path: str) -> str:
    return hid(f"buildfile:{path}")


def main() -> None:
    for path in APP_SOURCES + WIDGET_SOURCES:
        full = ROOT / path
        if not full.exists():
            raise SystemExit(f"Missing source file: {full}")

    app_product = hid("product:PowerSync.app")
    widget_product = hid("product:PowerSyncWidgets.appex")
    app_target = hid("target:PowerSync")
    widget_target = hid("target:PowerSyncWidgets")
    app_sources_phase = hid("phase:appSources")
    app_frameworks_phase = hid("phase:appFrameworks")
    app_resources_phase = hid("phase:appResources")
    app_embed_phase = hid("phase:appEmbed")
    widget_sources_phase = hid("phase:widgetSources")
    widget_frameworks_phase = hid("phase:widgetFrameworks")
    widget_resources_phase = hid("phase:widgetResources")
    project_id = hid("project:PowerSync")
    main_group = hid("group:main")
    products_group = hid("group:products")
    app_group = hid("group:PowerSync")
    widgets_group = hid("group:PowerSyncWidgets")
    app_config_list = hid("configlist:app")
    widget_config_list = hid("configlist:widget")
    project_config_list = hid("configlist:project")
    app_debug = hid("config:app:debug")
    app_release = hid("config:app:release")
    widget_debug = hid("config:widget:debug")
    widget_release = hid("config:widget:release")
    project_debug = hid("config:project:debug")
    project_release = hid("config:project:release")
    assets_ref = hid("fileref:Assets.xcassets")
    assets_build = hid("buildfile:Assets.xcassets")
    app_entitlements = hid("fileref:PowerSync.entitlements")
    widget_entitlements = hid("fileref:PowerSyncWidgets.entitlements")
    widget_info = hid("fileref:PowerSyncWidgets/Info.plist")
    embed_widget_build = hid("buildfile:embed:PowerSyncWidgets.appex")
    container_proxy = hid("containerproxy:widgets")
    target_dep = hid("targetdep:widgets")

    lines: list[str] = []
    lines.append("// !$*UTF8*$!")
    lines.append("{")
    lines.append("\tarchiveVersion = 1;")
    lines.append("\tclasses = {};")
    lines.append("\tobjectVersion = 56;")
    lines.append("\tobjects = {")
    lines.append("")
    lines.append("/* Begin PBXBuildFile section */")
    for path in APP_SOURCES:
        lines.append(
            f"\t\t{build_file(path)} /* {Path(path).name} in Sources */ = "
            f"{{isa = PBXBuildFile; fileRef = {file_ref(path)} /* {Path(path).name} */; }};"
        )
    for path in WIDGET_SOURCES:
        lines.append(
            f"\t\t{build_file(path)} /* {Path(path).name} in Sources */ = "
            f"{{isa = PBXBuildFile; fileRef = {file_ref(path)} /* {Path(path).name} */; }};"
        )
    lines.append(
        f"\t\t{assets_build} /* Assets.xcassets in Resources */ = "
        f"{{isa = PBXBuildFile; fileRef = {assets_ref} /* Assets.xcassets */; }};"
    )
    lines.append(
        f"\t\t{embed_widget_build} /* PowerSyncWidgets.appex in Embed Foundation Extensions */ = "
        f"{{isa = PBXBuildFile; fileRef = {widget_product} /* PowerSyncWidgets.appex */; "
        "settings = {ATTRIBUTES = (RemoveHeadersOnCopy, ); }; };"
    )
    lines.append("/* End PBXBuildFile section */")
    lines.append("")

    lines.append("/* Begin PBXContainerItemProxy section */")
    lines.append(f"\t\t{container_proxy} /* PBXContainerItemProxy */ = {{")
    lines.append("\t\t\tisa = PBXContainerItemProxy;")
    lines.append(f"\t\t\tcontainerPortal = {project_id} /* Project object */;")
    lines.append("\t\t\tproxyType = 1;")
    lines.append(f"\t\t\tremoteGlobalIDString = {widget_target};")
    lines.append('\t\t\tremoteInfo = PowerSyncWidgets;')
    lines.append("\t\t};")
    lines.append("/* End PBXContainerItemProxy section */")
    lines.append("")

    lines.append("/* Begin PBXCopyFilesBuildPhase section */")
    lines.append(f"\t\t{app_embed_phase} /* Embed Foundation Extensions */ = {{")
    lines.append("\t\t\tisa = PBXCopyFilesBuildPhase;")
    lines.append("\t\t\tbuildActionMask = 2147483647;")
    lines.append("\t\t\tdstPath = \"\";")
    lines.append("\t\t\tdstSubfolderSpec = 13;")
    lines.append("\t\t\tfiles = (")
    lines.append(f"\t\t\t\t{embed_widget_build} /* PowerSyncWidgets.appex in Embed Foundation Extensions */,")
    lines.append("\t\t\t);")
    lines.append('\t\t\tname = "Embed Foundation Extensions";')
    lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
    lines.append("\t\t};")
    lines.append("/* End PBXCopyFilesBuildPhase section */")
    lines.append("")

    lines.append("/* Begin PBXFileReference section */")
    lines.append(
        f"\t\t{app_product} /* PowerSync.app */ = "
        "{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; "
        'path = PowerSync.app; sourceTree = BUILT_PRODUCTS_DIR; };'
    )
    lines.append(
        f"\t\t{widget_product} /* PowerSyncWidgets.appex */ = "
        "{isa = PBXFileReference; explicitFileType = \"wrapper.app-extension\"; includeInIndex = 0; "
        'path = PowerSyncWidgets.appex; sourceTree = BUILT_PRODUCTS_DIR; };'
    )
    for path in APP_SOURCES + WIDGET_SOURCES:
        lines.append(
            f"\t\t{file_ref(path)} /* {Path(path).name} */ = "
            f"{{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; "
            f"path = {Path(path).name}; sourceTree = \"<group>\"; }};"
        )
    lines.append(
        f"\t\t{assets_ref} /* Assets.xcassets */ = "
        "{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; "
        "path = Assets.xcassets; sourceTree = \"<group>\"; };"
    )
    lines.append(
        f"\t\t{app_entitlements} /* PowerSync.entitlements */ = "
        "{isa = PBXFileReference; lastKnownFileType = text.plist.entitlements; "
        "path = PowerSync.entitlements; sourceTree = \"<group>\"; };"
    )
    lines.append(
        f"\t\t{widget_entitlements} /* PowerSyncWidgets.entitlements */ = "
        "{isa = PBXFileReference; lastKnownFileType = text.plist.entitlements; "
        "path = PowerSyncWidgets.entitlements; sourceTree = \"<group>\"; };"
    )
    lines.append(
        f"\t\t{widget_info} /* Info.plist */ = "
        "{isa = PBXFileReference; lastKnownFileType = text.plist.xml; "
        "path = Info.plist; sourceTree = \"<group>\"; };"
    )
    lines.append("/* End PBXFileReference section */")
    lines.append("")

    # Groups — flatten by folder name for simplicity
    def group_children(paths: list[str], prefix: str) -> list[str]:
        return [p for p in paths if p.startswith(prefix)]

    lines.append("/* Begin PBXGroup section */")
    lines.append(f"\t\t{main_group} = {{")
    lines.append("\t\t\tisa = PBXGroup;")
    lines.append("\t\t\tchildren = (")
    lines.append(f"\t\t\t\t{app_group} /* PowerSync */,")
    lines.append(f"\t\t\t\t{widgets_group} /* PowerSyncWidgets */,")
    lines.append(f"\t\t\t\t{products_group} /* Products */,")
    lines.append("\t\t\t);")
    lines.append('\t\t\tsourceTree = "<group>";')
    lines.append("\t\t};")

    lines.append(f"\t\t{products_group} /* Products */ = {{")
    lines.append("\t\t\tisa = PBXGroup;")
    lines.append("\t\t\tchildren = (")
    lines.append(f"\t\t\t\t{app_product} /* PowerSync.app */,")
    lines.append(f"\t\t\t\t{widget_product} /* PowerSyncWidgets.appex */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\tname = Products;")
    lines.append('\t\t\tsourceTree = "<group>";')
    lines.append("\t\t};")

    # Nested groups for app — put all app files in one group with path PowerSync
    # Using path-relative references: each file's path is just the filename, so we need nested groups.
    # Simpler approach: use full path in file refs.

    # Rebuild file refs with full relative paths for a flat-safe structure
    # Actually Xcode needs nested groups OR path = "Models/Foo.swift" with group path.

    # Use group path approach: PowerSync group has path = PowerSync, children use relative names in subfolders.

    folder_groups: dict[str, str] = {}

    def ensure_folder(folder: str) -> str:
        if folder in folder_groups:
            return folder_groups[folder]
        gid = hid(f"group:{folder}")
        folder_groups[folder] = gid
        return gid

    # Collect folders under PowerSync and PowerSyncWidgets
    app_folders = sorted({str(Path(p).parent) for p in APP_SOURCES})
    widget_folders = sorted({str(Path(p).parent) for p in WIDGET_SOURCES})

    lines.append(f"\t\t{app_group} /* PowerSync */ = {{")
    lines.append("\t\t\tisa = PBXGroup;")
    lines.append("\t\t\tchildren = (")
    # top-level items in PowerSync/
    for folder in app_folders:
        if folder == "PowerSync":
            continue
        if Path(folder).parent.name == "PowerSync" or folder.count("/") == 1:
            lines.append(f"\t\t\t\t{ensure_folder(folder)} /* {Path(folder).name} */,")
    for path in APP_SOURCES:
        if Path(path).parent.as_posix() == "PowerSync":
            lines.append(f"\t\t\t\t{file_ref(path)} /* {Path(path).name} */,")
    lines.append(f"\t\t\t\t{assets_ref} /* Assets.xcassets */,")
    lines.append(f"\t\t\t\t{app_entitlements} /* PowerSync.entitlements */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\tpath = PowerSync;")
    lines.append('\t\t\tsourceTree = "<group>";')
    lines.append("\t\t};")

    # Subfolders for app
    for folder in app_folders:
        if folder == "PowerSync":
            continue
        gid = ensure_folder(folder)
        lines.append(f"\t\t{gid} /* {Path(folder).name} */ = {{")
        lines.append("\t\t\tisa = PBXGroup;")
        lines.append("\t\t\tchildren = (")
        # nested child folders
        for child in app_folders:
            if Path(child).parent.as_posix() == folder:
                lines.append(f"\t\t\t\t{ensure_folder(child)} /* {Path(child).name} */,")
        for path in APP_SOURCES:
            if Path(path).parent.as_posix() == folder:
                lines.append(f"\t\t\t\t{file_ref(path)} /* {Path(path).name} */,")
        if folder == "PowerSync/Resources":
            pass
        lines.append("\t\t\t);")
        lines.append(f"\t\t\tpath = {Path(folder).name};")
        lines.append('\t\t\tsourceTree = "<group>";')
        lines.append("\t\t};")

    # Resources group for Assets — Assets is under PowerSync/Resources
    resources_group = ensure_folder("PowerSync/Resources")
    # Fix: Assets should be child of Resources. Reconstruct app_group children properly.

    lines.append(f"\t\t{widgets_group} /* PowerSyncWidgets */ = {{")
    lines.append("\t\t\tisa = PBXGroup;")
    lines.append("\t\t\tchildren = (")
    for path in WIDGET_SOURCES:
        lines.append(f"\t\t\t\t{file_ref(path)} /* {Path(path).name} */,")
    lines.append(f"\t\t\t\t{widget_entitlements} /* PowerSyncWidgets.entitlements */,")
    lines.append(f"\t\t\t\t{widget_info} /* Info.plist */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\tpath = PowerSyncWidgets;")
    lines.append('\t\t\tsourceTree = "<group>";')
    lines.append("\t\t};")
    lines.append("/* End PBXGroup section */")
    lines.append("")

    # Native targets
    lines.append("/* Begin PBXNativeTarget section */")
    lines.append(f"\t\t{app_target} /* PowerSync */ = {{")
    lines.append("\t\t\tisa = PBXNativeTarget;")
    lines.append('\t\t\tbuildConfigurationList = ' + app_config_list + ' /* Build configuration list for PBXNativeTarget "PowerSync" */;')
    lines.append("\t\t\tbuildPhases = (")
    lines.append(f"\t\t\t\t{app_sources_phase} /* Sources */,")
    lines.append(f"\t\t\t\t{app_frameworks_phase} /* Frameworks */,")
    lines.append(f"\t\t\t\t{app_resources_phase} /* Resources */,")
    lines.append(f"\t\t\t\t{app_embed_phase} /* Embed Foundation Extensions */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\tbuildRules = (")
    lines.append("\t\t\t);")
    lines.append("\t\t\tdependencies = (")
    lines.append(f"\t\t\t\t{target_dep} /* PBXTargetDependency */,")
    lines.append("\t\t\t);")
    lines.append('\t\t\tname = PowerSync;')
    lines.append('\t\t\tproductName = PowerSync;')
    lines.append(f"\t\t\tproductReference = {app_product} /* PowerSync.app */;")
    lines.append('\t\t\tproductType = "com.apple.product-type.application";')
    lines.append("\t\t};")

    lines.append(f"\t\t{widget_target} /* PowerSyncWidgets */ = {{")
    lines.append("\t\t\tisa = PBXNativeTarget;")
    lines.append('\t\t\tbuildConfigurationList = ' + widget_config_list + ' /* Build configuration list for PBXNativeTarget "PowerSyncWidgets" */;')
    lines.append("\t\t\tbuildPhases = (")
    lines.append(f"\t\t\t\t{widget_sources_phase} /* Sources */,")
    lines.append(f"\t\t\t\t{widget_frameworks_phase} /* Frameworks */,")
    lines.append(f"\t\t\t\t{widget_resources_phase} /* Resources */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\tbuildRules = (")
    lines.append("\t\t\t);")
    lines.append("\t\t\tdependencies = (")
    lines.append("\t\t\t);")
    lines.append('\t\t\tname = PowerSyncWidgets;')
    lines.append('\t\t\tproductName = PowerSyncWidgets;')
    lines.append(f"\t\t\tproductReference = {widget_product} /* PowerSyncWidgets.appex */;")
    lines.append('\t\t\tproductType = "com.apple.product-type.app-extension";')
    lines.append("\t\t};")
    lines.append("/* End PBXNativeTarget section */")
    lines.append("")

    lines.append("/* Begin PBXProject section */")
    lines.append(f"\t\t{project_id} /* Project object */ = {{")
    lines.append("\t\t\tisa = PBXProject;")
    lines.append("\t\t\tattributes = {")
    lines.append("\t\t\t\tBuildIndependentTargetsInParallel = 1;")
    lines.append('\t\t\t\tLastSwiftUpdateCheck = 2600;')
    lines.append('\t\t\t\tLastUpgradeCheck = 2600;')
    lines.append("\t\t\t\tTargetAttributes = {")
    lines.append(f"\t\t\t\t\t{app_target} = {{")
    lines.append("\t\t\t\t\t\tCreatedOnToolsVersion = 26.0;")
    lines.append("\t\t\t\t\t};")
    lines.append(f"\t\t\t\t\t{widget_target} = {{")
    lines.append("\t\t\t\t\t\tCreatedOnToolsVersion = 26.0;")
    lines.append("\t\t\t\t\t};")
    lines.append("\t\t\t\t};")
    lines.append("\t\t\t};")
    lines.append(f"\t\t\tbuildConfigurationList = {project_config_list} /* Build configuration list for PBXProject \"PowerSync\" */;")
    lines.append('\t\t\tcompatibilityVersion = "Xcode 14.0";')
    lines.append("\t\t\tdevelopmentRegion = en;")
    lines.append("\t\t\thasScannedForEncodings = 0;")
    lines.append("\t\t\tknownRegions = (")
    lines.append("\t\t\t\ten,")
    lines.append("\t\t\t\tBase,")
    lines.append("\t\t\t);")
    lines.append(f"\t\t\tmainGroup = {main_group};")
    lines.append(f"\t\t\tproductRefGroup = {products_group} /* Products */;")
    lines.append('\t\t\tprojectDirPath = "";')
    lines.append('\t\t\tprojectRoot = "";')
    lines.append("\t\t\ttargets = (")
    lines.append(f"\t\t\t\t{app_target} /* PowerSync */,")
    lines.append(f"\t\t\t\t{widget_target} /* PowerSyncWidgets */,")
    lines.append("\t\t\t);")
    lines.append("\t\t};")
    lines.append("/* End PBXProject section */")
    lines.append("")

    lines.append("/* Begin PBXResourcesBuildPhase section */")
    lines.append(f"\t\t{app_resources_phase} /* Resources */ = {{")
    lines.append("\t\t\tisa = PBXResourcesBuildPhase;")
    lines.append("\t\t\tbuildActionMask = 2147483647;")
    lines.append("\t\t\tfiles = (")
    lines.append(f"\t\t\t\t{assets_build} /* Assets.xcassets in Resources */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
    lines.append("\t\t};")
    lines.append(f"\t\t{widget_resources_phase} /* Resources */ = {{")
    lines.append("\t\t\tisa = PBXResourcesBuildPhase;")
    lines.append("\t\t\tbuildActionMask = 2147483647;")
    lines.append("\t\t\tfiles = (")
    lines.append("\t\t\t);")
    lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
    lines.append("\t\t};")
    lines.append("/* End PBXResourcesBuildPhase section */")
    lines.append("")

    lines.append("/* Begin PBXFrameworksBuildPhase section */")
    for phase in (app_frameworks_phase, widget_frameworks_phase):
        lines.append(f"\t\t{phase} /* Frameworks */ = {{")
        lines.append("\t\t\tisa = PBXFrameworksBuildPhase;")
        lines.append("\t\t\tbuildActionMask = 2147483647;")
        lines.append("\t\t\tfiles = (")
        lines.append("\t\t\t);")
        lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        lines.append("\t\t};")
    lines.append("/* End PBXFrameworksBuildPhase section */")
    lines.append("")

    lines.append("/* Begin PBXSourcesBuildPhase section */")
    lines.append(f"\t\t{app_sources_phase} /* Sources */ = {{")
    lines.append("\t\t\tisa = PBXSourcesBuildPhase;")
    lines.append("\t\t\tbuildActionMask = 2147483647;")
    lines.append("\t\t\tfiles = (")
    for path in APP_SOURCES:
        lines.append(f"\t\t\t\t{build_file(path)} /* {Path(path).name} in Sources */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
    lines.append("\t\t};")
    lines.append(f"\t\t{widget_sources_phase} /* Sources */ = {{")
    lines.append("\t\t\tisa = PBXSourcesBuildPhase;")
    lines.append("\t\t\tbuildActionMask = 2147483647;")
    lines.append("\t\t\tfiles = (")
    for path in WIDGET_SOURCES:
        lines.append(f"\t\t\t\t{build_file(path)} /* {Path(path).name} in Sources */,")
    lines.append("\t\t\t);")
    lines.append("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
    lines.append("\t\t};")
    lines.append("/* End PBXSourcesBuildPhase section */")
    lines.append("")

    lines.append("/* Begin PBXTargetDependency section */")
    lines.append(f"\t\t{target_dep} /* PBXTargetDependency */ = {{")
    lines.append("\t\t\tisa = PBXTargetDependency;")
    lines.append(f"\t\t\ttarget = {widget_target} /* PowerSyncWidgets */;")
    lines.append(f"\t\t\ttargetProxy = {container_proxy} /* PBXContainerItemProxy */;")
    lines.append("\t\t};")
    lines.append("/* End PBXTargetDependency section */")
    lines.append("")

    def xcconfig(name: str, cid: str, *, app: bool | None, debug: bool) -> list[str]:
        out = [f"\t\t{cid} /* {name} */ = {{", "\t\t\tisa = XCBuildConfiguration;", "\t\t\tbuildSettings = {"]
        if app is None:
            # project level
            out += [
                "\t\t\t\tALWAYS_SEARCH_USER_PATHS = NO;",
                "\t\t\t\tCLANG_ENABLE_MODULES = YES;",
                "\t\t\t\tCLANG_ENABLE_OBJC_ARC = YES;",
                f"\t\t\t\tCOPY_PHASE_STRIP = {'NO' if debug else 'YES'};",
                f"\t\t\t\tDEBUG_INFORMATION_FORMAT = {'dwarf' if debug else 'dwarf-with-dsym'};",
                "\t\t\t\tGCC_C_LANGUAGE_STANDARD = gnu17;",
                f"\t\t\t\tGCC_DYNAMIC_NO_PIC = NO;" if debug else "\t\t\t\tGCC_DYNAMIC_NO_PIC = YES;",
                "\t\t\t\tGCC_NO_COMMON_BLOCKS = YES;",
                f"\t\t\t\tGCC_OPTIMIZATION_LEVEL = {'0' if debug else 's'};",
                "\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 26.0;",
                "\t\t\t\tONLY_ACTIVE_ARCH = YES;" if debug else "\t\t\t\tONLY_ACTIVE_ARCH = NO;",
                "\t\t\t\tSDKROOT = iphoneos;",
                "\t\t\t\tSWIFT_VERSION = 6.0;",
                f"\t\t\t\tSWIFT_OPTIMIZATION_LEVEL = {'\"-Onone\"' if debug else '\"-O\"'};",
                "\t\t\t\tSWIFT_STRICT_CONCURRENCY = complete;",
            ]
            if debug:
                out.append('\t\t\t\tSWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";')
        elif app:
            out += [
                "\t\t\t\tASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;",
                "\t\t\t\tASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;",
                "\t\t\t\tCODE_SIGN_ENTITLEMENTS = PowerSync/PowerSync.entitlements;",
                "\t\t\t\tCODE_SIGN_STYLE = Automatic;",
                "\t\t\t\tCURRENT_PROJECT_VERSION = 1;",
                "\t\t\t\tDEVELOPMENT_TEAM = \"\";",
                "\t\t\t\tENABLE_PREVIEWS = YES;",
                "\t\t\t\tGENERATE_INFOPLIST_FILE = YES;",
                '\t\t\t\tINFOPLIST_KEY_CFBundleDisplayName = PowerSync;',
                '\t\t\t\tINFOPLIST_KEY_LSApplicationCategoryType = "public.app-category.utilities";',
                "\t\t\t\tINFOPLIST_KEY_NSSupportsLiveActivities = YES;",
                "\t\t\t\tINFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;",
                "\t\t\t\tINFOPLIST_KEY_UILaunchScreen_Generation = YES;",
                '\t\t\t\tINFOPLIST_KEY_UISupportedInterfaceOrientations = UIInterfaceOrientationPortrait;',
                "\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 26.0;",
                '\t\t\t\tLD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks");',
                "\t\t\t\tMARKETING_VERSION = 1.0.0;",
                "\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = cc.powersync.mobile;",
                "\t\t\t\tPRODUCT_NAME = \"$(TARGET_NAME)\";",
                "\t\t\t\tSUPPORTED_PLATFORMS = \"iphoneos iphonesimulator\";",
                "\t\t\t\tSUPPORTS_MACCATALYST = NO;",
                "\t\t\t\tSWIFT_EMIT_LOC_STRINGS = YES;",
                "\t\t\t\tSWIFT_STRICT_CONCURRENCY = complete;",
                "\t\t\t\tSWIFT_VERSION = 6.0;",
                "\t\t\t\tTARGETED_DEVICE_FAMILY = 1;",
            ]
        else:
            out += [
                "\t\t\t\tCODE_SIGN_ENTITLEMENTS = PowerSyncWidgets/PowerSyncWidgets.entitlements;",
                "\t\t\t\tCODE_SIGN_STYLE = Automatic;",
                "\t\t\t\tCURRENT_PROJECT_VERSION = 1;",
                "\t\t\t\tGENERATE_INFOPLIST_FILE = YES;",
                "\t\t\t\tINFOPLIST_FILE = PowerSyncWidgets/Info.plist;",
                "\t\t\t\tINFOPLIST_KEY_CFBundleDisplayName = PowerSync;",
                "\t\t\t\tINFOPLIST_KEY_NSSupportsLiveActivities = YES;",
                "\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 26.0;",
                '\t\t\t\tLD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks", "@executable_path/../../Frameworks");',
                "\t\t\t\tMARKETING_VERSION = 1.0.0;",
                "\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = cc.powersync.mobile.widgets;",
                "\t\t\t\tPRODUCT_NAME = \"$(TARGET_NAME)\";",
                "\t\t\t\tSKIP_INSTALL = YES;",
                "\t\t\t\tSUPPORTED_PLATFORMS = \"iphoneos iphonesimulator\";",
                "\t\t\t\tSWIFT_EMIT_LOC_STRINGS = YES;",
                "\t\t\t\tSWIFT_STRICT_CONCURRENCY = complete;",
                "\t\t\t\tSWIFT_VERSION = 6.0;",
                "\t\t\t\tTARGETED_DEVICE_FAMILY = 1;",
            ]
        out += ["\t\t\t};", f'\t\t\tname = {name};', "\t\t};"]
        return out

    lines.append("/* Begin XCBuildConfiguration section */")
    lines += xcconfig("Debug", project_debug, app=None, debug=True)
    lines += xcconfig("Release", project_release, app=None, debug=False)
    lines += xcconfig("Debug", app_debug, app=True, debug=True)
    lines += xcconfig("Release", app_release, app=True, debug=False)
    lines += xcconfig("Debug", widget_debug, app=False, debug=True)
    lines += xcconfig("Release", widget_release, app=False, debug=False)
    lines.append("/* End XCBuildConfiguration section */")
    lines.append("")

    lines.append("/* Begin XCConfigurationList section */")
    for list_id, debug_id, release_id, label in [
        (project_config_list, project_debug, project_release, 'PBXProject "PowerSync"'),
        (app_config_list, app_debug, app_release, 'PBXNativeTarget "PowerSync"'),
        (widget_config_list, widget_debug, widget_release, 'PBXNativeTarget "PowerSyncWidgets"'),
    ]:
        lines.append(f"\t\t{list_id} /* Build configuration list for {label} */ = {{")
        lines.append("\t\t\tisa = XCConfigurationList;")
        lines.append("\t\t\tbuildConfigurations = (")
        lines.append(f"\t\t\t\t{debug_id} /* Debug */,")
        lines.append(f"\t\t\t\t{release_id} /* Release */,")
        lines.append("\t\t\t);")
        lines.append("\t\t\tdefaultConfigurationIsVisible = 0;")
        lines.append('\t\t\tdefaultConfigurationName = Release;')
        lines.append("\t\t};")
    lines.append("/* End XCConfigurationList section */")
    lines.append("\t};")
    lines.append(f"\trootObject = {project_id} /* Project object */;")
    lines.append("}")

    # Fix Assets path — put Assets under Resources group
    # The assets_ref path is Assets.xcassets; it needs to live in PowerSync/Resources.
    # Update assets file ref path handling by rewriting Resources group inclusion.

    text = "\n".join(lines) + "\n"
    # Patch: Assets.xcassets fileRef should use path relative to Resources
    # We'll move Assets into Resources by adjusting app_group generation — already complex.
    # Simplest fix: change assets path to Resources/Assets.xcassets and put it in app group children with name.

    # Rewrite assets file reference to include Resources/ path from PowerSync root
    text = text.replace(
        f"{assets_ref} /* Assets.xcassets */ = {{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; path = Assets.xcassets; sourceTree = \"<group>\"; }};",
        f"{assets_ref} /* Assets.xcassets */ = {{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; name = Assets.xcassets; path = Resources/Assets.xcassets; sourceTree = \"<group>\"; }};",
    )

    PROJECT.parent.mkdir(parents=True, exist_ok=True)
    PROJECT.write_text(text)
    print(f"Wrote {PROJECT}")
    print(f"App sources: {len(APP_SOURCES)}, Widget sources: {len(WIDGET_SOURCES)}")


if __name__ == "__main__":
    main()
