import os
import adsk.core, adsk.fusion, adsk.cam, traceback

def run(context):
    ui = None
    try:

        # Get core objects
        app = adsk.core.Application.get()
        ui = app.userInterface

        # Ask user for SVG files
        folderDialog = ui.createFolderDialog()
        folderDialog.title = "Select SVG location"
        if folderDialog.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        folder = folderDialog.folder

        # Make new design document
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = app.activeProduct

        # Use current design
        #design = adsk.fusion.Design.cast(app.activeProduct)

        # Root component of active design
        rootComp = design.rootComponent

        # Infer how many layers are available from files
        names = {name for name in os.listdir(folder) if name.endswith(".svg")}
        numLayers = len(names)
        assert numLayers > 0
        for iz in range(numLayers):
            path = os.path.join(folder, f"{iz:04d}.svg")
            assert os.path.exists(path)

        # Create progress dialog
        progressDialog = ui.createProgressDialog()
        progressDialog.cancelButtonText = "Cancel"
        progressDialog.isBackgroundTranslucent = False
        progressDialog.isCancelButtonShown = True
        progressDialog.show("Importing layers", "%v/%m (%p)", 0, numLayers, 1)

        # Process each layer sequentially
        for iz in range(numLayers):

            # Check for cancellation
            if progressDialog.wasCancelled:
                break

            # Create new sketch on XY plane
            sketches = rootComp.sketches
            xyPlane = rootComp.xYConstructionPlane
            sketch: adsk.fusion.Sketch = sketches.add(xyPlane)
            sketch.name = f"Contour {iz:04d}"

            # Import SVG
            path = os.path.join(folder, f"{iz:04d}.svg")
            # TODO fix this magic number
            scale = 4 * 50.0 * 0.1 / 52.917
            if not sketch.importSVG(path, 0.0, 0.0, scale):
                raise ValueError("Failed to import SVG")

            # Check for cancellation
            if progressDialog.wasCancelled:
                break

            # Profiles share some loops, and we need to deduplicate them
            # As I don't know how to properly match them, here is a quick&dirty solution
            def getExtremePoint(loop: adsk.fusion.ProfileLoop):
                points = []
                for curve in loop.profileCurves:
                    evaluator = curve.geometry.evaluator
                    success, start, end = evaluator.getEndPoints()
                    for point in [start, end]:
                        point = start.x, start.y
                        points.append(point)
                return min(points)
            
            # For each profile, find inner loops (a.k.a. holes)
            profiles = {}
            parents = {}
            for profile in sketch.profiles:

                # Identify outer and inner loops
                outerLoop = None
                innerLoops = []
                for loop in profile.profileLoops:
                    if loop.isOuter:
                        assert outerLoop is None
                        outerLoop = loop
                    else:
                        innerLoops.append(loop)
                assert outerLoop is not None

                # Use key point as unique identifier, in order to find hierarchical relationships
                parentKey = getExtremePoint(outerLoop)
                profiles[parentKey] = profile
                for innerLoop in innerLoops:
                    key = getExtremePoint(innerLoop)
                    assert key not in parents
                    parents[key] = parentKey
            
            # These inner loops will also be the outer loop of another profile
            depths = {}
            for key in profiles.keys():
                depth = 0
                k = key
                while k in parents:
                    k = parents[k]
                    depth += 1
                depths[key] = depth

            # We only want to select even-numbered profiles
            collection = adsk.core.ObjectCollection.create()
            for key, profile in profiles.items():
                if depths[key] % 2 == 0:
                    collection.add(profile)

            # Create extrusion feature from sketch
            extrudes = rootComp.features.extrudeFeatures
            extrudeInput = extrudes.createInput(collection, adsk.fusion.FeatureOperations.JoinFeatureOperation)
            offset = adsk.core.ValueInput.createByReal(0.033 * iz)
            thickness = adsk.core.ValueInput.createByReal(0.033)
            extentStart = adsk.fusion.OffsetStartDefinition.create(offset)
            extentDistance = adsk.fusion.DistanceExtentDefinition.create(thickness)
            extrudeInput.setOneSideExtent(extentDistance, adsk.fusion.ExtentDirections.PositiveExtentDirection)
            extrudeInput.startExtent = extentStart
            extrudes.add(extrudeInput) 

            # Update progress bar
            progressDialog.progressValue += 1

        # Pack everything as a timeline group
        timeline = design.timeline
        group = timeline.timelineGroups.add(0, timeline.count - 1)
        group.name = "Layers"

        # Done
        progressDialog.hide()

    except:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))
